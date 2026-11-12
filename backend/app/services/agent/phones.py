"""
Phone numbers as a caller says them, checked before anything is sent to one.

A number the agent heard on a call is the weakest link in a WhatsApp send or
a recorded request: one digit short and the message reaches nobody, or a
stranger. So a number is normalised here (Urdu and Arabic-Indic digits,
spaces, dashes, `+92` and `0` forms), checked for shape, grouped for reading
back, and checked against what the caller actually said on this call.

Shape rules: a Pakistani mobile is eleven digits starting `03` (or `+923…`).
Anything else is eight to fifteen digits with a country code. A ten-digit
"0300123456" is refused, which is exactly the number that slipped through on
a real call.
"""
from __future__ import annotations

import re

#: Urdu (U+06F0–U+06F9) and Arabic-Indic (U+0660–U+0669) digits, as a
#: transcript of an Urdu speaker may carry them.
_DIGIT_MAP = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

#: How the agent says "the number they are calling from" in the `to` field.
CALLING_NUMBER = "calling_number"


def normalise_digits(text: str) -> str:
    """`text` with every Urdu or Arabic-Indic digit as its Western digit."""
    return (text or "").translate(_DIGIT_MAP)


def digits_only(text: str) -> str:
    return re.sub(r"\D", "", normalise_digits(text))


def parse(raw: str, country: str = "") -> tuple[str, str] | None:
    """`(E.164, national form)` for a number as the caller gave it, or None.

    `country` is the company's default country code ("92"); a national form
    (`03…`) is read in it. Without one, the Pakistani mobile shape is
    distinctive enough to take as Pakistani; any other national number is
    refused, because there is nothing to complete it with.
    """
    text = normalise_digits(raw or "").strip()
    if text.lower().replace(" ", "_") == CALLING_NUMBER:
        return None
    country = re.sub(r"\D", "", country or "")
    cleaned = re.sub(r"[^\d+]", "", text)
    if not cleaned:
        return None
    international = cleaned.startswith("+") or cleaned.startswith("00")
    digits = cleaned.lstrip("+")
    if digits.startswith("00"):
        digits = digits[2:]
    if not digits.isdigit():
        return None

    if international:
        if country and digits.startswith(country) and len(digits) > len(country):
            national = "0" + digits[len(country):]
            return _checked(digits, national, country)
        if digits.startswith("92") and len(digits) == 12:
            return _checked(digits, "0" + digits[2:], "92")
        return (f"+{digits}", digits) if 8 <= len(digits) <= 15 else None

    if digits.startswith("0"):
        if country:
            return _checked(country + digits[1:], digits, country)
        if digits.startswith("03") and len(digits) == 11:
            return _checked("92" + digits[1:], digits, "92")
        return None

    # No plus and no leading zero: "923001234567" or "3001234567".
    if country and digits.startswith(country) and len(digits) > len(country) + 6:
        return _checked(digits, "0" + digits[len(country):], country)
    if digits.startswith("92") and len(digits) == 12:
        return _checked(digits, "0" + digits[2:], "92")
    if country and 7 <= len(digits) <= 11:
        return _checked(country + digits, "0" + digits, country)
    return None


def _checked(full: str, national: str, country: str) -> tuple[str, str] | None:
    if country == "92":
        if national.startswith("03"):
            return (f"+{full}", national) if len(national) == 11 else None
        # A landline or a short code: ten or eleven digits in national form.
        return (f"+{full}", national) if 9 <= len(national) <= 12 else None
    return (f"+{full}", national) if 8 <= len(full) <= 15 else None


def grouped(national: str) -> str:
    """A number in the groups it is read back in: "0300 123 4567"."""
    digits = digits_only(national)
    if len(digits) == 11:
        return f"{digits[:4]} {digits[4:7]} {digits[7:]}"
    if len(digits) == 10:
        return f"{digits[:3]} {digits[3:6]} {digits[6:]}"
    return " ".join(digits[i:i + 3] for i in range(0, len(digits), 3))


def ending(number: str) -> str:
    """The last four digits, which is all a log or a read-back of the
    calling number needs."""
    digits = digits_only(number)
    return digits[-4:] if len(digits) >= 4 else digits


#: A run of digits long enough to be a phone number, with the spaces,
#: dashes and brackets people put in one. Amounts ("16,990,000") have
#: commas, which are not in the run, so they do not match.
_NUMBER_RUN = re.compile(r"\+?\d(?:[\d\s\-()]{7,}\d)")


def find_number(text: str, country: str = "") -> tuple[str, str] | None:
    """The first phone number dictated in `text`, parsed, or None.

    At least ten digits, so a price or a year is never taken for a number.
    """
    for match in _NUMBER_RUN.finditer(normalise_digits(text or "")):
        candidate = match.group(0)
        if len(digits_only(candidate)) < 10:
            continue
        parsed = parse(candidate, country)
        if parsed is not None:
            return parsed
    return None


def said_on_call(caller_turns: list[str], number: str) -> bool:
    """Whether the caller said this number on this call.

    The whole national form, or at least its last four digits, must appear
    in what the caller said, ignoring spaces and dashes: a number the agent
    produced from nowhere is the one it must not send to.
    """
    said = "".join(digits_only(turn) for turn in caller_turns)
    if not said:
        return False
    wanted = digits_only(number)
    if not wanted:
        return False
    if wanted in said:
        return True
    tail = wanted[-4:] if len(wanted) >= 4 else wanted
    return len(tail) == 4 and tail in said
