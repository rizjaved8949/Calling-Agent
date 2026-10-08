#!/usr/bin/env python3
"""
Run the whole stack: the API in `backend/` and the app in `frontend/`.

    python app.py

Both halves are normally started in separate terminals, which means two windows
to arrange, two to watch, and one of them quietly left running after the other
is stopped. This runs them as children of one process: their output is
interleaved with a prefix saying which is which, Ctrl+C stops both, and if
either one dies the other is brought down with it rather than left serving
half a stack.

Nothing here is needed to deploy. Render runs `backend/Dockerfile` and Vercel
builds `frontend/`; neither knows this file exists. It is for working on both
at once.

Options:

    --api-only / --web-only   run one half
    --install                 install dependencies first
    --api-port / --web-port   override the ports
    --no-color                plain output, for a log file
"""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from queue import Empty, Queue

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"

WINDOWS = os.name == "nt"

DEFAULT_API_PORT = 8000
DEFAULT_WEB_PORT = 5173


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


class Palette:
    """ANSI colours, or nothing at all.

    Honours NO_COLOR and a pipe, because a launcher's job is to make the two
    logs tellable apart, not to put escape codes in somebody's log file.
    """

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.api = self._code("36")      # cyan
        self.web = self._code("35")      # magenta
        self.good = self._code("32")
        self.warn = self._code("33")
        self.bad = self._code("31")
        self.dim = self._code("2")
        self.off = "\033[0m" if enabled else ""

    def _code(self, value: str) -> str:
        return f"\033[{value}m" if self.enabled else ""

    def paint(self, colour: str, text: str) -> str:
        return f"{colour}{text}{self.off}" if self.enabled else text


class ProcessGroup:
    """Ties the children's lifetime to this process, however it ends.

    The `finally` block below stops both services on Ctrl+C, on SIGTERM, and
    when one of them exits — but not when this process is killed outright, or
    the terminal window is closed. Then the children are orphaned, and they
    keep holding ports 8000 and 5173. The next run fails with an
    address-already-in-use error pointing at a server nobody can see.

    On Windows a Job Object with KILL_ON_JOB_CLOSE fixes it at the OS level:
    when the last handle to the job goes away — which includes this process
    dying for any reason at all — Windows terminates everything in it.

    On POSIX there is no equally simple equivalent, so this does nothing there
    and the signal handling covers the cases that matter.
    """

    def __init__(self) -> None:
        self.handle = None
        if not WINDOWS:
            return
        try:
            import ctypes
            from ctypes import wintypes

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [(name, ctypes.c_ulonglong) for name in
                            ("ReadOperationCount", "WriteOperationCount",
                             "OtherOperationCount", "ReadTransferCount",
                             "WriteTransferCount", "OtherTransferCount")]

            class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_longlong),
                    ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            self._ctypes = ctypes
            kernel32 = ctypes.windll.kernel32
            job = kernel32.CreateJobObjectW(None, None)
            if not job:
                return
            limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
            ok = kernel32.SetInformationJobObject(
                job, 9,  # JobObjectExtendedLimitInformation
                ctypes.byref(limits), ctypes.sizeof(limits),
            )
            self.handle = job if ok else None
        except Exception:  # noqa: BLE001 — a launcher must not fail over this
            self.handle = None

    def adopt(self, process: subprocess.Popen) -> None:
        if not self.handle or not WINDOWS:
            return
        try:
            ctypes = self._ctypes
            kernel32 = ctypes.windll.kernel32
            # PROCESS_SET_QUOTA | PROCESS_TERMINATE
            handle = kernel32.OpenProcess(0x0100 | 0x0001, False, process.pid)
            if handle:
                kernel32.AssignProcessToJobObject(self.handle, handle)
                kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001
            pass


def enable_ansi_on_windows() -> bool:
    """Turn on virtual terminal processing, which older consoles need.

    Windows Terminal and the VS Code terminal handle ANSI already; cmd.exe
    needs this flag set or the codes are printed literally.
    """
    if not WINDOWS:
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:  # noqa: BLE001 — colour is never worth an exception
        return False


# ---------------------------------------------------------------------------
# Child processes
# ---------------------------------------------------------------------------


class Service:
    """One half of the stack, and the thread draining its output."""

    def __init__(self, name: str, colour: str, command: list[str], cwd: Path,
                 env: dict[str, str] | None = None,
                 group: "ProcessGroup | None" = None) -> None:
        self.group = group
        self.name = name
        self.colour = colour
        self.command = command
        self.cwd = cwd
        self.env = env
        self.process: subprocess.Popen[str] | None = None
        self.reader: threading.Thread | None = None

    def start(self, lines: Queue) -> None:
        creation = 0
        preexec = None
        if WINDOWS:
            # Its own process group, so Ctrl+C in this console is not delivered
            # to the children directly — they are stopped deliberately, in
            # order, rather than racing this process to the exit.
            creation = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            preexec = os.setsid

        self.process = subprocess.Popen(
            self.command,
            cwd=str(self.cwd),
            env={**os.environ, **(self.env or {})},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creation,
            preexec_fn=preexec,
        )
        if self.group is not None:
            self.group.adopt(self.process)
        self.reader = threading.Thread(target=self._drain, args=(lines,), daemon=True)
        self.reader.start()

    def _drain(self, lines: Queue) -> None:
        assert self.process and self.process.stdout
        for line in self.process.stdout:
            lines.put((self.name, line.rstrip("\n")))
        lines.put((self.name, None))  # the stream closed: the process is going

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self) -> None:
        """Stop the process *and its children*.

        `npm run dev` is a wrapper: killing it on Windows leaves the node
        process underneath holding the port, so the next run fails with
        EADDRINUSE on a server nobody can see. taskkill /T takes the tree;
        on POSIX the process group does the same job.
        """
        if not self.process or self.process.poll() is not None:
            return
        pid = self.process.pid
        try:
            if WINDOWS:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True, check=False,
                )
            else:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            with_suppress(self.process.kill)


def with_suppress(fn) -> None:
    try:
        fn()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text("utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def npm_command() -> list[str] | None:
    r"""npm, resolved properly.

    On Windows `shutil.which("npm")` finds `C:\Program Files\nodejs\npm` —
    the **shell script**, which CreateProcess cannot run: it fails with
    "WinError 193: %1 is not a valid Win32 application", an error that says
    nothing about npm. The runnable one is `npm.cmd`, so it is asked for first.
    """
    for candidate in (("npm.cmd", "npm") if WINDOWS else ("npm",)):
        found = shutil.which(candidate)
        if found:
            return [found]
    return None


def check(paint: Palette, api: bool, web: bool, install: bool) -> list[str]:
    """Everything that would otherwise fail three seconds in. Returns problems."""
    problems: list[str] = []

    if api:
        if not (BACKEND / "app" / "main.py").exists():
            problems.append("backend/app/main.py is missing — is this the right folder?")
        try:
            import fastapi  # noqa: F401
            import uvicorn  # noqa: F401
        except ImportError:
            problems.append(
                "the API's dependencies are not installed — "
                "run: pip install -r backend/requirements.txt"
            )
        if not (BACKEND / ".env").exists():
            # A warning, not a problem: without it the API still runs, on the
            # local JSON store, and /health says so.
            say(paint, paint.warn, "setup",
                "backend/.env does not exist — the API will use the local JSON store. "
                "Copy backend/.env.example to backend/.env to change that.")

    if web:
        if not (FRONTEND / "package.json").exists():
            problems.append("frontend/package.json is missing — is this the right folder?")
        elif not (FRONTEND / "node_modules").exists():
            if install:
                pass  # installed below
            else:
                problems.append(
                    "frontend/node_modules is missing — "
                    "run: python app.py --install   (or: npm --prefix frontend install)"
                )
        if npm_command() is None:
            problems.append("npm was not found on PATH — install Node.js 20 or newer.")
        if not (FRONTEND / ".env.local").exists():
            say(paint, paint.warn, "setup",
                "frontend/.env.local does not exist — the app will run on its "
                "fixtures instead of the API. Copy frontend/.env.example to set VITE_API_URL.")

    return problems


def install_dependencies(paint: Palette, api: bool, web: bool) -> bool:
    if api:
        say(paint, paint.dim, "setup", "installing the API's dependencies ...")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
            cwd=str(BACKEND),
        )
        if result.returncode != 0:
            return False
    if web:
        npm = npm_command()
        if npm is None:
            return False
        say(paint, paint.dim, "setup", "installing the app's dependencies ...")
        result = subprocess.run([*npm, "install"], cwd=str(FRONTEND))
        if result.returncode != 0:
            return False
    return True


def probe_api(port: int, lines: Queue, stopping: threading.Event) -> None:
    """Wait until the API actually answers, and say so.

    Watching the process is not enough. Under `--reload` uvicorn runs a
    supervisor that outlives a failed bind, so a port it could never listen on
    leaves a process very much alive and nothing serving. Announcing a URL on
    that basis sends somebody to a page that will not load and then to look for
    the bug in the wrong half of the stack.

    A timeout warns rather than stops: `--reload` recovers on its own once a
    syntax error is fixed, and killing the app over a backend that is about to
    come back would be worse than waiting.
    """
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.time() + 40
    while not stopping.is_set() and time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    lines.put(("!ready", f"api  http://localhost:{port}"))
                    lines.put(("!dim", f"docs http://localhost:{port}/docs"))
                    return
        except (urllib.error.URLError, OSError, ValueError):
            pass
        time.sleep(0.5)
    if not stopping.is_set():
        lines.put(("!warn", f"the API never answered on port {port} — check the [api] lines above."))


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


def say(paint: Palette, colour: str, tag: str, message: str) -> None:
    print(f"{paint.paint(colour, f'[{tag}]'):<20} {message}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the Calling Agent API and app together.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--api-only", action="store_true", help="run only the backend")
    parser.add_argument("--web-only", action="store_true", help="run only the frontend")
    parser.add_argument("--install", action="store_true", help="install dependencies first")
    parser.add_argument("--api-port", type=int, default=None)
    parser.add_argument("--web-port", type=int, default=DEFAULT_WEB_PORT)
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args()

    use_colour = (
        not args.no_color
        and sys.stdout.isatty()
        and os.environ.get("NO_COLOR") is None
        and enable_ansi_on_windows()
    )
    paint = Palette(use_colour)

    want_api = not args.web_only
    want_web = not args.api_only
    if args.api_only and args.web_only:
        print("--api-only and --web-only cannot both be given.")
        return 2

    if args.install and not install_dependencies(paint, want_api, want_web):
        say(paint, paint.bad, "setup", "installing dependencies failed.")
        return 1

    problems = check(paint, want_api, want_web, args.install)
    if problems:
        for problem in problems:
            say(paint, paint.bad, "setup", problem)
        return 1

    backend_env = read_env_file(BACKEND / ".env")
    api_port = args.api_port or int(backend_env.get("PORT") or DEFAULT_API_PORT)

    group = ProcessGroup()
    services: list[Service] = []
    if want_api:
        services.append(Service(
            "api", paint.api,
            [sys.executable, "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", str(api_port), "--reload"],
            BACKEND, group=group,
        ))
    if want_web:
        npm = npm_command()
        assert npm is not None  # checked above
        services.append(Service(
            "web", paint.web,
            [*npm, "run", "dev", "--", "--port", str(args.web_port), "--strictPort"],
            FRONTEND, group=group,
        ))

    lines: Queue = Queue()
    print()
    for service in services:
        service.start(lines)
        say(paint, service.colour, service.name, f"starting in {service.cwd.name}/ ...")
    print()

    # Ctrl+C is handled here rather than by the children, so they are stopped
    # in order and the summary still prints.
    stopping = threading.Event()

    def on_interrupt(*_args) -> None:
        stopping.set()

    signal.signal(signal.SIGINT, on_interrupt)
    if not WINDOWS:
        signal.signal(signal.SIGTERM, on_interrupt)

    finished: set[str] = set()
    exit_code = 0
    web_announced = False

    if want_api:
        threading.Thread(
            target=probe_api, args=(api_port, lines, stopping), daemon=True
        ).start()

    try:
        while not stopping.is_set():
            try:
                name, line = lines.get(timeout=0.25)
            except Empty:
                # A child that exits on its own takes the other with it: half a
                # stack running is worse than none, because the half that is
                # missing looks like a bug in the half that is there.
                for service in services:
                    if service.process and service.process.poll() is not None:
                        if service.name not in finished:
                            finished.add(service.name)
                            code = service.process.returncode
                            exit_code = exit_code or code
                            say(paint, paint.bad, service.name,
                                f"stopped on its own (exit {code}) — stopping the rest.")
                            stopping.set()
                continue

            if line is None:
                finished.add(name)
                continue

            # Messages the prober puts on the queue, rather than child output.
            if name == "!ready":
                say(paint, paint.good, "ready", line)
                continue
            if name == "!dim":
                say(paint, paint.dim, "ready", line)
                continue
            if name == "!warn":
                say(paint, paint.warn, "ready", line)
                continue

            service = next(s for s in services if s.name == name)
            print(f"{paint.paint(service.colour, f'[{name}]'):<20} {line}", flush=True)

            # Vite prints its URL when it is listening, so that line is the
            # signal rather than a guess about timing.
            if "Local:" in line and not web_announced:
                web_announced = True
                say(paint, paint.good, "ready", f"app  http://localhost:{args.web_port}")
    finally:
        print()
        for service in services:
            if service.running:
                say(paint, service.colour, service.name, "stopping ...")
            service.stop()
        say(paint, paint.dim, "done", "both stopped.")

    return exit_code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
