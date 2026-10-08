#!/usr/bin/env python3
"""
Place a call without a phone.

    python scripts/call_probe.py --tenant <id> --wav speech.wav
    python scripts/call_probe.py --tenant <id> --replay <call id>
    python scripts/call_probe.py --tenant <id> --wav speech.wav --operator

Opens the same socket a carrier would, plays audio in at real-time pace, and
writes what comes back. Everything the chain does on a real call happens here:
the agent answers, the pacer releases its voice at line rate, both sides are
recorded on one timeline, the recording is transcoded and stored in the
company's Drive, and the transcript lands on the call row.

`--replay` feeds a recording the company already has, which is the closest
thing to a real caller available without dialling one.

`--operator` joins as a human partway through, so handover is exercised too.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _s in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError):
        _s.reconfigure(encoding="utf-8", errors="replace")

from app.services.agent.audio_rate import FRAME_BYTES, PHONE_RATE, pcm_to_wav  # noqa: E402

GREEN, YELLOW, RED, DIM, OFF = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"


def say(colour: str, tag: str, message: str) -> None:
    print(f"{colour}{tag:>10}{OFF}  {message}", flush=True)


def to_phone_audio(data: bytes) -> bytes:
    """Any WAV as 16 kHz PCM16 mono."""
    import numpy as np

    with wave.open(io.BytesIO(data)) as handle:
        channels, width, rate = handle.getnchannels(), handle.getsampwidth(), handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if width != 2:
        raise SystemExit(f"that WAV is {width * 8}-bit; 16-bit PCM is expected")
    samples = np.frombuffer(frames, dtype=np.int16)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1).astype(np.int16)
    if rate != PHONE_RATE:
        target = int(len(samples) * PHONE_RATE / rate)
        samples = np.interp(
            np.linspace(0, len(samples) - 1, target),
            np.arange(len(samples)), samples.astype(np.float32),
        ).astype(np.int16)
    return samples.tobytes()


async def run(base: str, admin_key: str, tenant_id: str, caller_pcm: bytes,
              out_path: Path, with_operator: bool) -> int:
    try:
        import websockets
    except ImportError:
        say(RED, "missing", "pip install websockets")
        return 1
    import httpx

    http = base.rstrip("/")
    ws = http.replace("https://", "wss://").replace("http://", "ws://")

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f"{http}/api/media/sessions",
            params={"companyId": tenant_id, "channel": "PHONE",
                    "counterparty": "+000000000", "direction": "INBOUND"},
        )
        body = response.json()
        if "callId" not in body:
            say(RED, "failed", f"could not open a session: {body}")
            return 1
    call_id, token = body["callId"], body["token"]
    say(DIM, "call", call_id)

    heard = bytearray()
    operator_said = bytearray()

    async with websockets.connect(f"{ws}/api/media/test/{call_id}?token={token}",
                                  max_size=None, ping_interval=20) as socket:
        say(GREEN, "connected", "the agent is answering")

        async def listen() -> None:
            try:
                async for frame in socket:
                    if isinstance(frame, bytes):
                        heard.extend(frame)
            except Exception:  # noqa: BLE001 — the socket closing ends the call
                pass

        listener = asyncio.create_task(listen())

        # Give the greeting a moment to arrive before talking over it.
        await asyncio.sleep(2.5)

        seconds = len(caller_pcm) / (PHONE_RATE * 2)
        say(DIM, "speaking", f"{seconds:.1f}s of caller audio, in real time")
        started = time.monotonic()
        operator = None
        for i in range(0, len(caller_pcm), FRAME_BYTES):
            await socket.send(caller_pcm[i : i + FRAME_BYTES])
            await asyncio.sleep(0.02)
            if with_operator and operator is None and time.monotonic() - started > seconds / 2:
                operator = await join_as_operator(ws, call_id, token, operator_said)

        # Let the reply finish arriving.
        quiet = time.monotonic()
        last = 0
        while time.monotonic() - quiet < 6.0:
            await asyncio.sleep(0.3)
            if len(heard) != last:
                last, quiet = len(heard), time.monotonic()

        if operator:
            operator.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await operator
        with contextlib.suppress(Exception):
            await socket.send("hangup")
        listener.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await listener

    say(DIM, "hung up", "waiting for the call to settle")
    await asyncio.sleep(5)

    if heard:
        out_path.write_bytes(pcm_to_wav(bytes(heard), PHONE_RATE))
        say(GREEN, "agent said", f"{len(heard) / (PHONE_RATE * 2):.1f}s -> {out_path}")
    else:
        say(RED, "silence", "the agent produced no audio")

    async with httpx.AsyncClient(timeout=120) as client:
        head = {"X-Admin-Key": admin_key, "X-Company-Id": tenant_id}
        call = (await client.get(f"{http}/api/calls/{call_id}", headers=head)).json()
    say(DIM, "status", f"{call.get('status')}  {call.get('durationSeconds')}s")
    say(DIM, "recording", f"{call.get('recording', {}).get('state')}"
                          f"  {call.get('recording', {}).get('bytes') or 0:,} bytes")
    if call.get("transcript"):
        print()
        for line in call["transcript"].splitlines()[:12]:
            say(DIM, "", line[:96])
    return 0 if heard else 1


async def join_as_operator(ws: str, call_id: str, token: str, captured: bytearray):
    """Take the call over mid-sentence, as a person clicking the button would."""
    import websockets

    async def run_operator() -> None:
        try:
            async with websockets.connect(
                f"{ws}/api/media/operator/{call_id}?token={token}", max_size=None
            ) as socket:
                say(YELLOW, "operator", "a person took the call")
                async for frame in socket:
                    if isinstance(frame, bytes):
                        captured.extend(frame)
        except Exception:  # noqa: BLE001
            pass

    return asyncio.create_task(run_operator())


async def recording_of(tenant_id: str, call_id: str) -> bytes:
    from app.repositories import calls as call_repo
    from app.repositories import tenants as tenant_repo
    from app.services import storage

    tenant = await tenant_repo.get(tenant_id)
    call = await call_repo.get_call(tenant_id, call_id)
    if tenant is None or call is None or not call.recording_path:
        raise SystemExit("that call has no stored recording")
    audio = await storage.get(tenant, call.recording_path)
    if not audio:
        raise SystemExit("the recording could not be read")
    return audio


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tenant", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--wav", type=Path)
    source.add_argument("--replay", help="a call id whose recording to play in")
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--admin-key", default="")
    parser.add_argument("--operator", action="store_true",
                        help="a person takes over halfway through")
    parser.add_argument("--out", type=Path, default=Path("call-reply.wav"))
    args = parser.parse_args()

    admin = args.admin_key
    if not admin:
        import os

        from app.config import settings
        admin = settings.admin_api_key or os.environ.get("ADMIN_API_KEY", "")

    if args.wav:
        caller = to_phone_audio(args.wav.read_bytes())
    else:
        caller = to_phone_audio(asyncio.run(recording_of(args.tenant, args.replay)))

    return asyncio.run(run(args.base, admin, args.tenant, caller, args.out, args.operator))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
