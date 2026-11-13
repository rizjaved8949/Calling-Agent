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


# ---------------------------------------------------------------------------
# Numbers as people say them
# ---------------------------------------------------------------------------
#
# "bees lakh das hazar", "बीस लाख दस हज़ार", "بیس لاکھ دس ہزار" and "20 lakh 10
# hazar" are the same number. Read as values, so a transcript rewritten by a
# model can be checked for a changed number whichever way each side writes
# it, and so what the agent says can be checked against what the material
# and the calculator gave it.

_UNIT_WORDS: dict[str, float] = {}


def _unit(value: float, *words: str) -> None:
    for word in words:
        _UNIT_WORDS[word] = value


_unit(0.5, "aadha", "adha", "आधा", "آدھا")
_unit(1, "ek", "aik", "एक", "ایک")
_unit(1.25, "sawa", "सवा", "سوا")
_unit(1.5, "dedh", "derh", "डेढ़", "डेढ", "ڈیڑھ")
_unit(2, "do", "दो", "دو")
_unit(2.5, "dhai", "dhaai", "ढाई", "ڈھائی")
_unit(3, "teen", "tin", "तीन", "تین")
_unit(4, "chaar", "char", "चार", "چار")
_unit(5, "paanch", "panch", "pach", "पांच", "पाँच", "پانچ")
_unit(6, "chhe", "che", "chay", "chhay", "chha", "छह", "छः", "छे", "چھ", "چھے")
_unit(7, "saat", "sat", "सात", "سات")
_unit(8, "aath", "ath", "आठ", "آٹھ")
_unit(9, "nau", "nou", "नौ", "نو")
_unit(10, "das", "dus", "दस", "دس")
_unit(11, "gyarah", "gyara", "ग्यारह", "گیارہ")
_unit(12, "barah", "bara", "बारह", "بارہ")
_unit(13, "terah", "tera", "तेरह", "تیرہ")
_unit(14, "chaudah", "chauda", "चौदह", "چودہ")
_unit(15, "pandrah", "pandra", "पंद्रह", "पन्द्रह", "پندرہ")
_unit(16, "solah", "sola", "सोलह", "سولہ")
_unit(17, "satrah", "satra", "सत्रह", "سترہ")
_unit(18, "atharah", "athara", "अठारह", "اٹھارہ")
_unit(19, "unnis", "unees", "उन्नीस", "انیس")
_unit(20, "bees", "bis", "बीस", "بیس")
_unit(21, "ikkis", "ekkis", "इक्कीस", "اکیس")
_unit(22, "bais", "bayees", "बाईस", "بائیس")
_unit(23, "teis", "tayees", "तेईस", "تئیس")
_unit(24, "chaubis", "chobis", "चौबीस", "چوبیس")
_unit(25, "pachis", "pachees", "पच्चीस", "پچیس")
_unit(26, "chhabbis", "chabbis", "छब्बीस", "چھبیس")
_unit(27, "sattais", "satais", "सत्ताईस", "ستائیس")
_unit(28, "athais", "atthais", "अट्ठाईस", "اٹھائیس")
_unit(29, "untis", "untees", "उनतीस", "انتیس")
_unit(30, "tees", "tis", "तीस", "تیس")
_unit(40, "chalis", "chaalis", "चालीस", "چالیس")
_unit(50, "pachas", "pachaas", "पचास", "پچاس")
_unit(60, "saath", "sath", "साठ", "ساٹھ")
_unit(70, "sattar", "satter", "सत्तर", "ستر")
_unit(80, "assi", "asi", "अस्सी", "اسی")
_unit(90, "nabbe", "navve", "नब्बे", "نوے")

_SCALE_WORDS: dict[str, int] = {}
for _multiplier, _names in ((100, ("sau", "sao", "सौ", "سو")),
                            (1_000, ("hazar", "hazaar", "hajar", "हज़ार", "हजार", "ہزار")),
                            (100_000, ("lakh", "laakh", "lac", "lakhs", "लाख", "لاکھ")),
                            (10_000_000, ("crore", "crores", "karod", "karor", "करोड़", "करोड", "کروڑ"))):
    for _name in _names:
        _SCALE_WORDS[_name] = _multiplier

_DEVANAGARI_DIGITS = {chr(0x0966 + i): str(i) for i in range(10)}
_URDU_DIGITS = {chr(0x06F0 + i): str(i) for i in range(10)}
_URDU_DIGITS.update({chr(0x0660 + i): str(i) for i in range(10)})
#: A number with its commas and decimals, or a word. `\w` alone drops the
#: vowel signs and nuktas of Devanagari and Urdu, so "बीस" would come apart
#: into "ब" and "स"; the letter blocks are named, without their punctuation.
_TOKEN = re.compile(
    r"\d[\d,]*\d(?:\.\d+)?|\d(?:\.\d+)?"
    r"|[\wऀ-ॣ०-ॿ؀-؋؍-؞ؠ-ۓە-ۿ]+")


def _western_digits(text: str) -> str:
    return "".join(_DEVANAGARI_DIGITS.get(ch, _URDU_DIGITS.get(ch, ch)) for ch in text or "")


def _spoken_groups(text: str) -> list[tuple[int, int, float, bool]]:
    """Each number in `text`: (start, end, value, said with a scale of a
    thousand or more). Digits and words alike, with hazar, lakh and crore
    multiplying what comes before them: "1 crore 47 lakh 90 hazar"."""
    plain = _western_digits(text)
    groups: list[tuple[int, int, float, bool]] = []
    total = 0.0
    pending: float | None = None
    start = end = -1
    big = False
    in_number = False

    def flush() -> None:
        nonlocal total, pending, start, end, big, in_number
        if in_number:
            groups.append((start, end, total + (pending or 0.0), big))
        total, pending, start, end, big, in_number = 0.0, None, -1, -1, False, False

    for match in _TOKEN.finditer(plain):
        token = match.group(0)
        lowered = token.lower()
        if lowered in _SCALE_WORDS:
            scale = _SCALE_WORDS[lowered]
            if not in_number:
                start = match.start()
            total += (pending if pending is not None else 1.0) * scale
            pending = None
            end = match.end()
            big = big or scale >= 1_000
            in_number = True
            continue
        value: float | None = None
        if token[0].isdigit():
            try:
                value = float(token.replace(",", ""))
            except ValueError:
                value = None
        elif lowered in _UNIT_WORDS:
            value = _UNIT_WORDS[lowered]
        if value is None:
            flush()
            continue
        if pending is not None:
            flush()                                   # "do teen": two numbers
        if not in_number:
            start = match.start()
        pending = value
        end = match.end()
        in_number = True
    flush()
    return groups


def values_in(text: str) -> list[float]:
    """Every number said in `text`, digits or words, as values."""
    return [round(value, 2) for _s, _e, value, _big in _spoken_groups(text)]


def spoken_amounts(text: str) -> list[tuple[str, int]]:
    """Each amount said with hazar, lakh or crore: ("20 lakh 10 hazar", 2010000)."""
    return [(text[s:e], int(round(value))) for s, e, value, big in _spoken_groups(text)
            if big and value >= 1_000]


def material_values(text: str) -> set[int]:
    """Every rupee amount the material states: marked with a currency, or
    said in hazar, lakh or crore."""
    found: set[int] = set()
    for pattern in (_PREFIXED, _SUFFIXED):
        for match in pattern.finditer(text or ""):
            value = _value(match.group("num"), match.group("scale"))
            if value is not None:
                found.add(int(round(value)))
    for _phrase, value in spoken_amounts(text or ""):
        found.add(value)
    return found


# ---------------------------------------------------------------------------
# Budgets and priced items
# ---------------------------------------------------------------------------

_BUDGET_WORDS = ("budget", "bajat", "bajet", "bajut", "بجٹ", "बजट")
_PRICE_LABEL_NOISE = re.compile(
    r"^(?:the\s+)?(?:price|prices|qeemat|keemat|cost|rate|ex[- ]showroom|starting)\s+(?:of|for)?\s*"
    r"|\s+(?:is|hai|at|from|starts?|starting|se|par|:|-|–|—)+\s*$", re.I)


def stated_budget(text: str) -> int | None:
    """The budget a caller states: the largest amount in a line that says
    "budget" (or "bajat"), when it is at least a lakh. None otherwise."""
    lowered = (text or "").lower()
    if not any(word in lowered for word in _BUDGET_WORDS):
        return None
    amounts_said = [int(round(v)) for v in values_in(text) if v >= 100_000]
    return max(amounts_said) if amounts_said else None


def priced_items(text: str) -> list[tuple[str, int]]:
    """Each thing the material prices, with its price: ("SEALION 7 Premium",
    15490000). Read line by line from whatever names the amount on its left:
    a label before a colon, a table cell, "the price of X is". Nothing is
    assumed about the company's products."""
    items: list[tuple[str, int]] = []
    for line in (text or "").splitlines():
        for pattern in (_PREFIXED, _SUFFIXED):
            for match in pattern.finditer(line):
                value = _value(match.group("num"), match.group("scale"))
                if value is None or value < 1_000:
                    continue
                before = line[:match.start()]
                # The nearest cell or clause with words in it, left of the amount.
                for separator in ("|", ";", "•", " - ", " – ", " — "):
                    if separator in before:
                        cells = [c for c in before.split(separator) if c.strip(" \t-*#•|:–—")]
                        before = cells[-1] if cells else ""
                if ":" in before:
                    before = before.rsplit(":", 1)[0] if before.rstrip().endswith(":") \
                        else before.rsplit(":", 1)[-1]
                label = _PRICE_LABEL_NOISE.sub("", before.strip(" \t-*#•|:–—"))
                label = _PRICE_LABEL_NOISE.sub("", label.strip(" \t-*#•|:–—")).strip()
                words = label.split()
                if not words or len(words) > 6 or not any(w[0].isalpha() for w in words):
                    continue
                items.append((" ".join(words), int(round(value))))
    return items


#: Labels that price a charge, not a product; not held against a budget.
_NOT_A_PRODUCT = {
    "booking", "advance", "registration", "token", "insurance", "delivery", "service",
    "charger", "installation", "fee", "fees", "deposit", "down", "price", "total",
    "monthly", "instalment", "installment", "tax", "taxes", "duty", "discount", "refund",
    "cancellation", "transfer", "number", "plate", "maintenance", "warranty", "charging",
}


def _is_model_word(word: str) -> bool:
    return word.isdigit() or (len(word) <= 2 and any(c.isdigit() for c in word))


def families_of(label: str) -> list[str]:
    """The product a priced label names, as the words a caller or agent would
    use for it: "SEALION 7 Premium" is "sealion 7", "SEAL Dynamic" is "seal",
    and "BYD SEAL Dynamic" is both "byd seal" and "seal", so a brand prefix
    in the material does not hide the model."""
    words = label.lower().split()
    if not words or words[0] in _NOT_A_PRODUCT:
        return []

    def family(ws: list[str]) -> str:
        if len(ws) > 1 and _is_model_word(ws[1]):
            return " ".join(ws[:2])
        return ws[0]

    keys = []
    if len(words) >= 3 and not _is_model_word(words[1]):
        keys.append(" ".join(words[:2]))
        if words[1] not in _NOT_A_PRODUCT:
            keys.append(family(words[1:]))
    else:
        keys.append(family(words))
    return [k for k in dict.fromkeys(keys) if len(k) >= 3]


def family_of(label: str) -> str:
    """The first family a label names, or ""."""
    keys = families_of(label)
    return keys[0] if keys else ""


def family_prices(text: str) -> dict[str, list[int]]:
    """Every product family the material prices, with all its prices."""
    found: dict[str, list[int]] = {}
    for label, price in priced_items(text):
        for key in families_of(label):
            found.setdefault(key, []).append(price)
    return found


def families_named(spoken: str, families: dict[str, list[int]]) -> list[str]:
    """Which priced families the agent's words name, as whole words."""
    lowered = " " + re.sub(r"[^\w\s]", " ", (spoken or "").lower()) + " "
    lowered = re.sub(r"\s+", " ", lowered)
    return [key for key in families if f" {key} " in lowered]
