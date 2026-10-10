"""
Which model carries the conversation.

One place that turns an engine name into a session. Everything downstream —
`session.py`, the pacer, the recorder, the transports — holds a session and
never asks what is behind it, because every engine is written to the same
interface: `feed`, `say`, `run`, `close`, `wait_until_ready`, `live`, and the
four callbacks.

That is the whole abstraction, and it is deliberately thin. An engine is not a
plugin system; it is a class with six methods, and adding one should be a file
and a line here rather than a framework.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Protocol

from .gemini import AgentPersona, GeminiLiveSession, LiveSettings

log = logging.getLogger(__name__)

GEMINI = "gemini"
OPENAI = "openai"
DEFAULT = GEMINI


class Session(Protocol):
    """What every engine must be. `session.py` depends on exactly this."""

    @property
    def live(self) -> bool: ...
    async def wait_until_ready(self, timeout: float = 30.0) -> None: ...
    def close(self) -> None: ...
    def feed(self, pcm16: bytes) -> None: ...
    async def say(self, text: str) -> None: ...
    async def run(self) -> None: ...


def build(
    settings: LiveSettings,
    persona: AgentPersona,
    *,
    on_audio: Callable[[bytes, int], None] | None = None,
    on_transcript: Callable[[str, str], None] | None = None,
    on_interrupted: Callable[[], None] | None = None,
    on_tool: Callable[[str, dict[str, Any]], Awaitable[str]] | None = None,
    label: str = "session",
) -> Session:
    """A session on whichever engine these settings name.

    An unknown engine falls back to the default rather than failing: the
    alternative is a caller holding a silent line because of a typo in a
    settings screen, and the log line says which call it happened on.
    """
    callbacks = {
        "on_audio": on_audio,
        "on_transcript": on_transcript,
        "on_interrupted": on_interrupted,
        "on_tool": on_tool,
        "label": label,
    }

    engine = (settings.engine or DEFAULT).strip().lower()
    if engine == OPENAI:
        from .openai_realtime import OpenAIRealtimeSession

        return OpenAIRealtimeSession(settings, persona, **callbacks)

    if engine != GEMINI:
        log.warning("%s: %r is not an engine this build knows, using %s",
                    label, settings.engine, DEFAULT)
    return GeminiLiveSession(settings, persona, **callbacks)


def known() -> set[str]:
    return {GEMINI, OPENAI}
