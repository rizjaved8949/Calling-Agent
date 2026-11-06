"""
Hindi script out of transcripts: Urdu and Hindi as Roman Urdu.

Spoken Urdu and spoken Hindi are one language to a speech recogniser, and when
it has to choose a script it chooses Devanagari. The caller spoke Urdu; the
transcript, the Unanswered list and the call page then show them Hindi script
that the people reading them cannot read. Telling the model which language it
is does not reach the caller's side: that transcript comes from the
recogniser, not from anything the prompt says.

So anything written in Devanagari is rewritten as Roman Urdu, the way people
here type it. Text in any other script — English, Urdu in its own script,
Arabic — is left exactly as it was.

Two ways, for two moments:

* `romanize` — letter by letter, instant and offline. Used wherever a
  transcript is shown, so Devanagari never reaches a screen, including
  transcripts saved before this existed and a call still in progress.
* `to_roman_urdu` — the text model, once per call after it ends. Spells the
  way a person would ("registration", not "rajistreshan"); falls back to
  `romanize` for any line it does not return cleanly.
"""
from __future__ import annotations

import asyncio
import difflib
import json
import logging
import re
from collections import Counter

log = logging.getLogger(__name__)

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")

#: How long the text model gets before the letter-by-letter version stands.
MODEL_TIMEOUT_SECONDS = 20.0


def has_devanagari(text: str) -> bool:
    return bool(text) and bool(_DEVANAGARI.search(text))


# ---------------------------------------------------------------------------
# Letter by letter
# ---------------------------------------------------------------------------

_NUKTA = "़"
_VIRAMA = "्"
_ANUSVARA = "ं"
_CHANDRABINDU = "ँ"
_VISARGA = "ः"

# Spelled the way Roman Urdu spells them: "w" not "v", "sh" for both sibilants,
# "f" for फ, which Urdu speakers say as f.
_CONSONANTS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n",
    "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n",
    "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n", "ऩ": "n",
    "प": "p", "फ": "f", "ब": "b", "भ": "bh", "म": "m",
    "य": "y", "र": "r", "ऱ": "r", "ल": "l", "ळ": "l", "व": "w",
    "श": "sh", "ष": "sh", "स": "s", "ह": "h",
    # With the nukta, as one character or as two.
    "क़": "q", "ख़": "kh", "ग़": "gh", "ज़": "z", "ड़": "r", "ढ़": "rh",
    "फ़": "f", "य़": "y",
}
_NUKTA_FORMS = {
    "क": "q", "ख": "kh", "ग": "gh", "ज": "z", "ड": "r", "ढ": "rh", "फ": "f",
    "य": "y",
}

_VOWELS = {
    "अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo", "ऋ": "ri",
    "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au", "ऑ": "o", "ऍ": "e",
}
_SIGNS = {
    "ा": "aa", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo", "ृ": "ri",
    "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ॉ": "o", "ॅ": "e",
}
_OTHER = {"।": ".", "॥": ".", "ॐ": "om"}
_DIGITS = {chr(0x0966 + i): str(i) for i in range(10)}

_LIPS = {"p", "f", "b", "bh", "m"}


def _word(word: str) -> str:
    """One Devanagari word in Roman letters.

    Built as syllables first — consonant plus vowel, where the vowel is the
    inherent "a" unless a sign or a virama says otherwise — so the inherent
    vowel can then be dropped where Urdu does not say it.
    """
    # Each unit: [consonant, vowel, nasal]; a lone vowel has consonant "".
    units: list[list[str]] = []
    i = 0
    while i < len(word):
        ch = word[i]
        nxt = word[i + 1] if i + 1 < len(word) else ""
        if ch in _CONSONANTS:
            sound = _CONSONANTS[ch]
            if nxt == _NUKTA:
                sound = _NUKTA_FORMS.get(ch, sound)
                i += 1
                nxt = word[i + 1] if i + 1 < len(word) else ""
            if nxt == _VIRAMA:
                units.append([sound, "", ""])
                i += 2
                continue
            if nxt in _SIGNS:
                units.append([sound, _SIGNS[nxt], ""])
                i += 2
                continue
            units.append([sound, "a*", ""])         # inherent vowel, may drop
            i += 1
            continue
        if ch in _VOWELS:
            units.append(["", _VOWELS[ch], ""])
        elif ch in (_ANUSVARA, _CHANDRABINDU):
            if units:
                units[-1][2] = "n"
        elif ch == _VISARGA:
            units.append(["h", "", ""])
        elif ch in _DIGITS:
            units.append([_DIGITS[ch], "", ""])
        elif ch in _OTHER:
            units.append([_OTHER[ch], "", ""])
        elif ch in (_NUKTA, _VIRAMA) or "ऀ" <= ch <= "ॿ":
            pass                                     # a stray mark: nothing to say
        else:
            units.append([ch, "", ""])
        i += 1

    def spoken(k: int) -> bool:
        return 0 <= k < len(units) and units[k][1] not in ("", "a*")

    def voiced(k: int) -> bool:
        """Carries a vowel, counting an inherent one not yet decided."""
        return 0 <= k < len(units) and units[k][1] != ""

    # The inherent vowel is not said at the end of a word ("kam", not "kama"),
    # nor between two consonants that each sit next to a vowel ("itna", not
    # "itana"). Right to left, so each decision sees the one after it.
    last = len(units) - 1
    while last >= 0 and not units[last][0] and not units[last][1]:
        last -= 1
    for k in range(last, -1, -1):
        if units[k][1] != "a*":
            continue
        if units[k][2]:                              # "an" before a nasal stays
            units[k][1] = "a"
            continue
        if k == last and k > 0:
            units[k][1] = ""
        elif 0 < k < last and voiced(k - 1) and spoken(k + 1) and units[k + 1][0]:
            units[k][1] = ""
        else:
            units[k][1] = "a"

    out: list[str] = []
    for k, (sound, vowel, nasal) in enumerate(units):
        final = k == last
        if vowel == "aa" and final and not nasal:
            vowel = "a"                              # "kya", "mera"
        elif vowel == "ee" and final:
            vowel = "i"                              # "ki", "bhi", "nahin"
        elif vowel == "oo" and final and not nasal:
            vowel = "u"
        if nasal:
            if vowel == "e" and final:
                vowel = "ei"                         # "mein", "dein"; "lagenge"
            following = units[k + 1][0] if k + 1 < len(units) else ""
            nasal = "m" if following in _LIPS else "n"
        out.append(sound + vowel + nasal)
    return "".join(out)


_WORD = re.compile(r"[ऀ-ॿ]+")


def romanize(text: str) -> str:
    """Devanagari in `text` as Roman Urdu; everything else untouched."""
    if not has_devanagari(text):
        return text or ""
    return _WORD.sub(lambda m: _word(m.group(0)), text)


def readable(transcript: str) -> str:
    """A transcript as it should be shown. Cheap enough to call on every read."""
    return romanize(transcript) if has_devanagari(transcript) else (transcript or "")


# ---------------------------------------------------------------------------
# With the text model
# ---------------------------------------------------------------------------

_INSTRUCTIONS = (
    "You convert phone-call transcript lines from Devanagari script into Roman "
    "Urdu: Urdu written in English letters, the way people in Pakistan type it "
    "in WhatsApp messages (for example \"Mujhe registration ki fees bata dein\"). "
    "The speakers are Urdu speakers whose words were written down in Hindi "
    "script by mistake. Use Urdu spellings (\"qeemat\", \"zaroorat\", "
    "\"akhrajaat\"), and never add or remove words. Write English words "
    "that appear in Devanagari as normal English (\"रजिस्ट्रेशन\" is "
    "\"registration\"). Keep any text that is not Devanagari exactly as it is, "
    "and keep punctuation. Only the spelling changes: keep every number, "
    "amount and name exactly as said, a number spoken as a word stays a word "
    "(\"पाँच सौ\" is \"paanch sau\", never \"500\"), and do not translate, "
    "summarise, reorder or correct what was said.\n\n"
    "You receive a JSON array of strings. Reply with only a JSON array of the "
    "same length, each item the converted version of the item at the same "
    "position."
)


async def to_roman_urdu(lines: list[str], *, api_key: str, model: str) -> list[str]:
    """`lines` with Devanagari rewritten as Roman Urdu by the text model.

    Same length and order as given. Any line the model leaves out, garbles or
    still writes in Devanagari is converted letter by letter instead, so the
    result never contains Devanagari whatever the model does.
    """
    targets = [k for k, line in enumerate(lines) if has_devanagari(line)]
    result = list(lines)
    if not targets:
        return result

    converted: list[str] | None = None
    if api_key.strip():
        try:
            converted = await asyncio.wait_for(
                _ask_model([lines[k] for k in targets], api_key=api_key, model=model),
                MODEL_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 — the fallback below still works
            log.info("roman urdu: the text model was not used (%s: %s)",
                     type(exc).__name__, str(exc)[:120])
            converted = None

    for n, k in enumerate(targets):
        better = converted[n] if converted and n < len(converted) else ""
        good = isinstance(better, str) and faithful(lines[k], better)
        if converted and not good:
            log.info("roman urdu: line %d kept letter by letter (the model's "
                     "version did not match what was said)", k)
        result[k] = better.strip() if good else romanize(lines[k])
    return result


# ---------------------------------------------------------------------------
# Whether the model only changed the spelling
# ---------------------------------------------------------------------------
#
# The model is asked to respell, but a model asked to respell can also
# translate, tidy, summarise, turn "paanch sau" into "500" or swap one name or
# number for another. A transcript is a record of what someone said, so its
# version is used only when it checks out against the original, with the
# letter-by-letter version, which cannot change meaning, as the yardstick.

#: How close the model's line must sound to the letter-by-letter one (0-1).
#: A faithful respelling scores about 0.9; a translation, or a clause added
#: or dropped, scores well under 0.8.
MIN_SIMILARITY = 0.75

# Spellings of the same sound, so "keemat" and "qeemat", "rajistreshan" and
# "registration" are compared on what was said.
_SAME_SOUND = (("ph", "f"), ("q", "k"), ("v", "w"), ("z", "j"), ("x", "ks"),
               ("c", "k"), ("ee", "i"), ("oo", "u"), ("aa", "a"))

# Number words as they are said. Each one in the original must still be
# there, as the same word, in the model's version.
_NUMBER_WORDS = frozenset("""
    एक दो तीन चार पांच पाँच छह छः छे सात आठ नौ दस ग्यारह बारह तेरह चौदह पंद्रह
    पन्द्रह सोलह सत्रह अठारह उन्नीस बीस पच्चीस तीस चालीस पचास साठ सत्तर अस्सी
    नब्बे सौ हज़ार हजार लाख करोड़ करोड आधा डेढ़ डेढ ढाई सवा पौने पहला दूसरा
    तीसरा चौथा
""".split())

_LATIN_WORD = re.compile(r"[A-Za-z0-9]+")
_DIGIT_RUN = re.compile(r"\d+")
_KEPT_MARKS = "?%₹$€£+/@"


def _sounds(text: str) -> str:
    """The consonants of `text`, with spellings of one sound folded together."""
    # A nasal ending is written either way: "nahin"/"nahi", "main"/"mai".
    text = re.sub(r"(?<=[aeiou])n\b", "", text.lower())
    text = re.sub(r"[^a-z0-9]", "", text)
    for variant, same in _SAME_SOUND:
        text = text.replace(variant, same)
    return re.sub(r"[aeiouyh]", "", text)


def _digits(text: str) -> list[str]:
    plain = "".join(_DIGITS.get(ch, ch) for ch in text)
    return sorted(_DIGIT_RUN.findall(plain))


def faithful(original: str, candidate: str) -> bool:
    """Whether `candidate` is `original` respelled, and nothing more.

    Every check is against what was said. A line that fails costs only the
    nicer spelling: the letter-by-letter version is used instead.
    """
    candidate = (candidate or "").strip()
    if not candidate or has_devanagari(candidate):
        return False
    plain = romanize(original)

    # Numbers: the same figures, and no new ones ("paanch sau" is not "500").
    if _digits(original) != _digits(candidate):
        return False
    # Marks that carry meaning: a question stays a question, and "%", "₹"
    # and the like stay.
    if any(original.count(m) != candidate.count(m) for m in _KEPT_MARKS):
        return False
    # Anything already in Latin letters (a name, a brand, an English word)
    # comes back unchanged.
    have = Counter(w.lower() for w in _LATIN_WORD.findall(candidate))
    kept = Counter(w.lower() for w in _LATIN_WORD.findall(_WORD.sub(" ", original)))
    if any(have[word] < times for word, times in kept.items()):
        return False
    # Number words: each one still said, as itself.
    said = [_sounds(w) for w in candidate.split()]
    for word in _WORD.findall(original):
        if word in _NUMBER_WORDS:
            wanted = _sounds(_word(word))
            if not any(difflib.SequenceMatcher(None, wanted, w).ratio() >= 0.75
                       for w in said):
                return False
    # Length: a respelling has about as many words, allowing for "आपकी"
    # written as "aap ki". Nothing added, nothing dropped.
    words, theirs = len(plain.split()), len(candidate.split())
    if abs(words - theirs) > max(2, round(words * 0.15)):
        return False
    # Content: it still sounds like what was said, as a whole...
    if difflib.SequenceMatcher(
            None, _sounds(plain), _sounds(candidate)).ratio() < MIN_SIMILARITY:
        return False
    # ...and word by word, so one name or one word swapped for another in an
    # otherwise faithful line ("Ali" for "Ahmed") is caught too.
    return _every_word_kept(plain.split(), candidate.split())


#: How alike one said word and its respelling must sound (0-1). Loanwords
#: land around 0.65 ("rajistreshan" / "registration"); different words, 0.
MIN_WORD_SIMILARITY = 0.5


def _alike(said: str, written: str) -> float:
    a, b = _sounds(said), _sounds(written)
    if not a and not b:
        return 1.0                       # "है" / "hai": nothing to compare
    if not a or not b:
        return 0.0
    if len(a) == 1 or len(b) == 1:
        return 1.0 if a == b else 0.0    # short words must match outright
    return difflib.SequenceMatcher(None, a, b).ratio()


def _every_word_kept(said: list[str], written: list[str]) -> bool:
    """Whether the words line up one to one, each sounding like itself.

    Aligned in order, allowing one said word written as two ("aapki" as
    "aap ki") or two as one. A said word with no counterpart, or matched to
    one that sounds different, means the line was changed, not respelled.
    """
    n, m = len(said), len(written)
    worst = float("inf")
    # best[i][j]: (words that did not line up, alignment cost) for the first
    # i said and j written words.
    best: list[list[tuple[int, float]]] = [[(10**6, worst)] * (m + 1) for _ in range(n + 1)]
    best[0][0] = (0, 0.0)
    for i in range(n + 1):
        for j in range(m + 1):
            here = best[i][j]
            if here[0] >= 10**6:
                continue

            def step(ni: int, nj: int, sim: float) -> None:
                bad = here[0] + (0 if sim >= MIN_WORD_SIMILARITY else 1)
                cand = (bad, here[1] + (1 - sim))
                if cand < best[ni][nj]:
                    best[ni][nj] = cand

            if i < n and j < m:
                step(i + 1, j + 1, _alike(said[i], written[j]))
            if i < n and j + 1 < m:
                step(i + 1, j + 2, _alike(said[i], written[j] + written[j + 1]))
            if i + 1 < n and j < m:
                step(i + 2, j + 1, _alike(said[i] + said[i + 1], written[j]))
            if i < n:
                step(i + 1, j, 0.0)      # a said word left out
            if j < m:
                step(i, j + 1, 0.0)      # a word added
    return best[n][m][0] == 0


async def _ask_model(lines: list[str], *, api_key: str, model: str) -> list[str] | None:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key.strip())
    response = await client.aio.models.generate_content(
        model=model,
        contents=[types.Content(role="user", parts=[
            types.Part(text=json.dumps(lines, ensure_ascii=False))])],
        config=types.GenerateContentConfig(
            system_instruction=_INSTRUCTIONS,
            temperature=0.1,
            response_mime_type="application/json",
            max_output_tokens=8192,
        ),
    )
    parsed = json.loads((getattr(response, "text", "") or "").strip())
    if not isinstance(parsed, list) or len(parsed) != len(lines):
        return None
    return [str(item) for item in parsed]
