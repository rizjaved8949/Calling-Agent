#!/usr/bin/env python3
"""
Talk to the agent without a phone.

    python scripts/agent_probe.py --say "when do admissions close?"
    python scripts/agent_probe.py --wav some-speech.wav
    python scripts/agent_probe.py --call <call id> --tenant <phone number id>

The third form is the interesting one: it replays a real recorded call into the
model as if the caller were speaking now, which exercises the whole path on
real speech in the real language rather than on a synthetic tone.

Writes the agent's reply to a .wav you can play, and prints both sides'
transcripts. No WebRTC, no carrier, no TURN — just the model, so this proves
the key, the quota and the persona before any of the call plumbing exists.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.services.agent.audio_rate import FRAME_BYTES, PHONE_RATE, pcm_to_wav  # noqa: E402
from app.services.agent.gemini import (  # noqa: E402
    AgentPersona,
    AgentUnavailable,
    GeminiLiveSession,
    LiveSettings,
    available,
)

GREEN, YELLOW, RED, DIM, OFF = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"


# A Windows console is cp1252 by default, and the agent answers in whatever
# language the caller used. Printing an Urdu transcript to it raises
# UnicodeEncodeError and takes the probe down *after* a successful call —
# failing at the last line, on the one result that proves it worked.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def say(colour: str, tag: str, message: str) -> None:
    print(f"{colour}{tag:>10}{OFF}  {message}", flush=True)


def read_wav_as_phone_audio(data: bytes) -> bytes:
    """Any mono/stereo WAV, as 16 kHz PCM16 mono.

    Real recordings arrive at whatever the carrier used, so the rate is read
    from the file rather than assumed — feeding 48 kHz audio to a model
    expecting 16 kHz produces a chipmunk that transcribes as nonsense.
    """
    import numpy as np

    with wave.open(io.BytesIO(data)) as handle:
        channels, width, rate = handle.getnchannels(), handle.getsampwidth(), handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if width != 2:
        raise SystemExit(f"that WAV is {width * 8}-bit; this expects 16-bit PCM")

    samples = np.frombuffer(frames, dtype=np.int16)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1).astype(np.int16)
    if rate != PHONE_RATE:
        # Linear interpolation is enough here: this is a test harness, and the
        # bridge itself uses the filtered converter.
        target = int(len(samples) * PHONE_RATE / rate)
        samples = np.interp(
            np.linspace(0, len(samples) - 1, target),
            np.arange(len(samples)),
            samples.astype(np.float32),
        ).astype(np.int16)
    return samples.tobytes()


async def fetch_call_audio(tenant_id: str, call_id: str) -> bytes:
    from app.repositories import calls as call_repo
    from app.repositories import tenants as tenant_repo
    from app.services import storage

    tenant = await tenant_repo.get(tenant_id)
    if tenant is None:
        raise SystemExit(f"no company registered on {tenant_id}")
    call = await call_repo.get_call(tenant_id, call_id)
    if call is None or not call.recording_path:
        raise SystemExit(f"call {call_id} has no stored recording")
    audio = await storage.get(tenant, call.recording_path)
    if not audio:
        raise SystemExit("the recording could not be read back")
    return audio


async def probe(persona_text: str, greeting: str, voice: str,
                caller_pcm: bytes, opening: str, out_path: Path) -> int:
    if not available():
        say(RED, "missing", "google-genai is not installed — pip install -r requirements.txt")
        return 1
    if not settings.gemini_api_key.strip():
        say(RED, "missing", "GEMINI_API_KEY is not set")
        return 1

    reply = bytearray()
    heard: list[str] = []
    said: list[str] = []

    def on_audio(pcm: bytes, _rate: int) -> None:
        reply.extend(pcm)

    def on_transcript(who: str, text: str) -> None:
        (heard if who == "caller" else said).append(text)

    session = GeminiLiveSession(
        LiveSettings(model=settings.gemini_live_model, api_key=settings.gemini_api_key.strip()),
        AgentPersona(instructions=persona_text, greeting=greeting, voice=voice),
        on_audio=on_audio,
        on_transcript=on_transcript,
        label="probe",
    )

    say(DIM, "model", settings.gemini_live_model)
    runner = asyncio.create_task(session.run())
    started = time.monotonic()
    try:
        await session.wait_until_ready(timeout=40)
        if not session.live:
            await runner  # surfaces the real reason
            return 1
        say(GREEN, "connected", f"in {time.monotonic() - started:.1f}s")

        if opening:
            await session.say(opening)
            say(DIM, "sent", f"text: {opening!r}")

        if caller_pcm:
            seconds = len(caller_pcm) / (PHONE_RATE * 2)
            say(DIM, "speaking", f"{seconds:.1f}s of caller audio, in real time")
            # Paced at 20 ms per frame against the wall clock. Sent as fast as
            # the loop can manage, the model's turn detector sees one long
            # burst and answers as if the caller never paused.
            for i in range(0, len(caller_pcm), FRAME_BYTES):
                session.feed(caller_pcm[i : i + FRAME_BYTES])
                await asyncio.sleep(0.02)

        # Let the reply finish arriving: stop once the audio stops growing.
        quiet_since = time.monotonic()
        last = 0
        while time.monotonic() - quiet_since < 4.0:
            await asyncio.sleep(0.25)
            if len(reply) != last:
                last, quiet_since = len(reply), time.monotonic()
            if runner.done():
                break
    except asyncio.TimeoutError:
        say(RED, "timeout", "the model did not connect within 40s")
        return 1
    except AgentUnavailable as exc:
        say(RED, "failed", str(exc))
        return 1
    finally:
        session.close()
        runner.cancel()
        with_suppress(runner)

    print()
    if heard:
        say(DIM, "heard", "".join(heard).strip()[:400])
    if said:
        say(GREEN, "agent", "".join(said).strip()[:400])
    if not reply:
        say(RED, "no audio", "the model connected but produced no speech")
        return 1

    out_path.write_bytes(pcm_to_wav(bytes(reply), 24_000))
    seconds = len(reply) / (24_000 * 2)
    say(GREEN, "written", f"{out_path}  ({seconds:.1f}s, {len(reply) / 1024:,.0f} KB)")
    return 0


def with_suppress(task) -> None:
    with contextlib.suppress(Exception):
        task.cancel()


DEFAULT_PERSONA = (
    "You are a helpful voice agent answering a phone call for a business. "
    "Speak naturally and briefly, the way someone does on the telephone. "
    "Never read out punctuation or formatting."
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--say", help="text for the caller to 'say'")
    source.add_argument("--wav", type=Path, help="a WAV of real speech to play in")
    source.add_argument("--call", help="replay a stored recording by call id")
    parser.add_argument("--tenant", help="required with --call")
    parser.add_argument("--persona", default=DEFAULT_PERSONA)
    parser.add_argument("--greeting", default="")
    parser.add_argument("--voice", default="", help="a Gemini prebuilt voice name")
    parser.add_argument("--out", type=Path, default=Path("agent-reply.wav"))
    args = parser.parse_args()

    caller_pcm = b""
    if args.wav:
        caller_pcm = read_wav_as_phone_audio(args.wav.read_bytes())
    elif args.call:
        if not args.tenant:
            return parser.error("--call needs --tenant")
        caller_pcm = read_wav_as_phone_audio(
            asyncio.run(fetch_call_audio(args.tenant, args.call))
        )

    return asyncio.run(
        probe(args.persona, args.greeting, args.voice, caller_pcm, args.say or "", args.out)
    )


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
