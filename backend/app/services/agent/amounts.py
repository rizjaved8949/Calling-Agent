"""
Rupee amounts the way they are said: lakh and crore.

The material writes prices with Western grouping, `PKR 8,990,000`, and the
voice model reads that aloud by guessing the groups. On real calls it said
"9 lakh 90 hazar" for 8,990,000 and "7 lakh 29 hazar" for 7,290,000: a tenth
of the real price, stated with confidence. So every rupee amount in the
material gets its spoken form written after it, once, in brackets:

    PKR 8,990,000 (89 lakh 90 hazar)

and the agent is told to say the words in the brackets. The stored documents
are untouched; this runs on the copy handed to the model.

Only amounts with a currency marker are touched (`PKR`, `Rs.`, `rupees`): a
bare `8,990,000` could be a kilometre reading. Text already in lakh or crore,
and an amount already followed by its bracket, are left exactly as they are,
so running this twice changes nothing.
"""
from __future__ import annotations

import re

#: What the scale words multiply by. Lakh, crore and hazar are not here on
#: purpose: an amount already written that way is already spoken.
_SCALES = {
    "million": 1_000_000, "mn": 1_000_000,
    "billion": 1_000_000_000, "bn": 1_000_000_000,
    "thousand": 1_000, "k": 1_000,
}
_SPOKEN_SCALES = {"lakh", "lakhs", "lac", "lacs", "crore", "crores", "cr", "hazar"}

_NUMBER = r"(?P<num>\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
_SCALE = (r"(?:\s*(?P<scale>million|mn|billion|bn|thousand|k|lakhs?|lacs?|"
          r"crores?|cr|hazar)\b)?")
#: `PKR 8,990,000`, `Rs. 12,000`, `Rupees 1.75 million`, `₨12,000`.
_PREFIXED = re.compile(rf"(?:\b(?:PKR|Rs\.?|Rupees?)|₨)\s*{_NUMBER}{_SCALE}", re.I)
#: `8,990,000 rupees`, `12,000 Rs`, `1.75 million PKR`.
_SUFFIXED = re.compile(rf"\b{_NUMBER}{_SCALE}\s*(?:rupees?|PKR|Rs\.?)(?![A-Za-z])", re.I)
#: A bracket that already says it in words, right after the amount.
_ALREADY = re.compile(r"^\s*\([^)]*\b(?:lakh|crore|hazar)\b[^)]*\)", re.I)


def spoken_pkr(amount: float | int) -> str:
    """An amount in the words an Urdu speaker says: `7290000` -> "72 lakh 90 hazar".

    Below a thousand the figure is its own name. Paise are dropped: nobody
    reads a price list to the paisa, and "12 hazar 500 rupay 50 paise" is
    not how anyone says it.
    """
    n = int(round(float(amount)))
    sign = "minus " if n < 0 else ""
    n = abs(n)
    if n < 1000:
        return f"{sign}{n}"
    crore, rest = divmod(n, 10_000_000)
    lakh, rest = divmod(rest, 100_000)
    hazar, rest = divmod(rest, 1_000)
    parts = []
    if crore:
        parts.append(f"{crore} crore")
    if lakh:
        parts.append(f"{lakh} lakh")
    if hazar:
        parts.append(f"{hazar} hazar")
    if rest:
        parts.append(str(rest))
    return sign + " ".join(parts)


def _value(num: str, scale: str | None) -> float | None:
    """The rupees an amount names, or None if it is already in spoken words."""
    scale = (scale or "").lower()
    if scale in _SPOKEN_SCALES:
        return None
    try:
        value = float(num.replace(",", ""))
    except ValueError:
        return None
    return value * _SCALES.get(scale, 1)


def _annotated(match: re.Match[str]) -> str:
    found = match.group(0)
    if _ALREADY.match(match.string[match.end():]):
        return found                         # already says it in words
    value = _value(match.group("num"), match.group("scale"))
    if value is None or value < 1000:
        return found
    words = spoken_pkr(value)
    if words == str(int(round(value))):
        return found
    return f"{found} ({words})"


def annotate(text: str) -> str:
    """`text` with the spoken form written once after each rupee amount."""
    if not text:
        return text
    text = _PREFIXED.sub(_annotated, text)
    return _SUFFIXED.sub(_annotated, text)


def has_amounts(text: str) -> bool:
    """Whether `annotate` would find anything to say."""
    return bool(text) and bool(_PREFIXED.search(text) or _SUFFIXED.search(text))


def wants_spoken(language: str, country_code: str) -> bool:
    """Whether this agent's material should carry the spoken forms: an Urdu
    agent, or a company in Pakistan. An English agent for a London clinic
    keeps its figures as they are."""
    spoken = (language or "").strip().lower().startswith("ur")
    return spoken or re.sub(r"\D", "", country_code or "") == "92"
