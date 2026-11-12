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


#: Digits as the agent speaks them when reading a number back, in English
#: and in Urdu. Only a run of three or more counts, so "do" (two, or "give")
#: and "ek" on their own are never turned into digits.
_SPOKEN_DIGIT = {
    "zero": "0", "sifar": "0", "sifr": "0", "oh": "0",
    "one": "1", "ek": "1", "aik": "1", "two": "2", "do": "2",
    "three": "3", "teen": "3", "tin": "3", "four": "4", "chaar": "4", "char": "4",
    "five": "5", "paanch": "5", "panch": "5", "six": "6", "chhe": "6", "che": "6",
    "chay": "6", "chhay": "6", "seven": "7", "saat": "7", "sat": "7",
    "eight": "8", "aath": "8", "ath": "8", "nine": "9", "nau": "9", "nou": "9",
}
_SPOKEN_RUN = re.compile(
    r"(?:\b(?:" + "|".join(sorted(_SPOKEN_DIGIT, key=len, reverse=True)) + r")\b[\s,.\-]*){3,}",
    re.IGNORECASE,
)


def spoken_digits(text: str) -> str:
    """`text` with every run of three or more spoken digits ("zero three
    zero zero, one two three") written as digits."""
    def as_digits(match: re.Match) -> str:
        words = re.findall(r"[A-Za-z]+", match.group(0))
        return "".join(_SPOKEN_DIGIT.get(w.lower(), "") for w in words) + " "

    return _SPOKEN_RUN.sub(as_digits, text or "")


#: How many digits a mobile has in its national form, by country code.
MOBILE_DIGITS = {"92": 11}
_DIGIT_RUN = re.compile(r"\+?\d(?:[\d\s\-()]*\d)?")


def wrong_length_mobile(text: str, country: str = "") -> int | None:
    """The digit count of a mobile number in `text` that is the wrong length
    for the country, or None.

    A Pakistani mobile is 11 digits starting 03. A caller who says ten is
    read back as if complete unless somebody counts: on a real call the
    agent read "0300 123456" back and asked whether it was right.
    """
    country = re.sub(r"\D", "", country or "") or "92"
    expected = MOBILE_DIGITS.get(country)
    if expected is None:
        return None
    for match in _DIGIT_RUN.finditer(spoken_digits(normalise_digits(text or ""))):
        digits = digits_only(match.group(0))
        if len(digits) < 8 or len(digits) > 14:
            continue                               # not a number, or two run together
        if digits.startswith("00"):
            digits = digits[2:]
        if digits.startswith(country) and len(digits) > len(country) + 6:
            national = "0" + digits[len(country):]
        elif digits.startswith("0"):
            national = digits
        elif digits.startswith("3") and 9 <= len(digits) <= 12:
            national = "0" + digits
        else:
            continue
        if national.startswith("03") and len(national) != expected:
            return len(national)
    return None


def read_back_in(agent_text: str, number: str) -> bool:
    """Whether the agent's turn read this number back, in digits or in
    words: the whole number, or at least its last four digits."""
    return said_on_call([spoken_digits(agent_text)], number)


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
