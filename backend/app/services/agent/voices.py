"""
The voices an agent can speak in.

The settings screen used to ask for this as free text, which meant knowing that
"Kore" is a voice and "female" is not, and getting a silent or default-voiced
call for any typo. The engine accepts thirty names; this is the subset worth
offering, grouped the way somebody actually chooses — a man's voice or a
woman's — with a word on how each sounds.

The descriptions and the grouping are Google's, not measurements of our own,
so the screen says to hear one on a test call before putting it on a line. A
name not in this list is still accepted and passed through: the engine may add
voices faster than this file is updated, and refusing one it supports would be
worse than offering one we have not described.
"""
from __future__ import annotations

from typing import Any

# (name, how it sounds)
FEMALE: list[tuple[str, str]] = [
    ("Kore", "Firm and clear — a good default for a helpline"),
    ("Aoede", "Breezy and light"),
    ("Leda", "Youthful"),
    ("Zephyr", "Bright"),
    ("Autonoe", "Bright and warm"),
    ("Callirrhoe", "Easy-going"),
]

MALE: list[tuple[str, str]] = [
    ("Puck", "Upbeat — a good default for a helpline"),
    ("Charon", "Informative and steady"),
    ("Orus", "Firm"),
    ("Fenrir", "Lively"),
    ("Enceladus", "Breathy and soft"),
    ("Iapetus", "Clear"),
]

# Every name the engine takes, so a voice chosen before this list existed is
# not quietly dropped on the next save.
ALL_NAMES = {name for name, _ in FEMALE + MALE} | {
    "Umbriel", "Algieba", "Despina", "Erinome", "Algenib", "Rasalgethi",
    "Laomedeia", "Achernar", "Alnilam", "Schedar", "Gacrux", "Pulcherrima",
    "Achird", "Zubenelgenubi", "Vindemiatrix", "Sadachbia", "Sadaltager",
    "Sulafat",
}


def catalogue() -> dict[str, Any]:
    """What the settings screen shows."""
    return {
        "voices": [
            {"name": name, "gender": "female", "description": description}
            for name, description in FEMALE
        ] + [
            {"name": name, "gender": "male", "description": description}
            for name, description in MALE
        ],
        "note": (
            "These are the voice engine's own voices. How one sounds in Urdu or "
            "mixed Urdu and English is worth hearing on a test call before you "
            "put it on a line."
        ),
    }


def known(name: str) -> bool:
    return name.strip() in ALL_NAMES
