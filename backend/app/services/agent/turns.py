"""
Reading the conversation so far, to decide what the agent may do next.

The model says it was asked to send a message, or that the caller is done;
the transcript says whether that is so. These helpers turn the transcript
fragments into turns and answer the small, plain questions the tool guards
ask: did the caller say yes, did they say goodbye, did the agent offer
WhatsApp, did the caller ask for it, did they say to use the number they are
calling from.

Word lists rather than a model, in Urdu (Roman and script), Hindi and
English, because a guard that calls a model is a guard that stalls the call.
They are deliberately generous with yes and strict with no: a "nahi" anywhere
in the answer wins.
"""
from __future__ import annotations

import re

Turn = tuple[str, str]

_PUNCT = re.compile(r"[^\w\s']+", re.UNICODE)


def turns(transcript: list[Turn]) -> list[Turn]:
    """Consecutive fragments from one speaker as one turn, system lines dropped."""
    out: list[list[str]] = []
    for who, text in transcript:
        text = (text or "").strip()
        if not text or who not in ("caller", "agent"):
            continue
        if out and out[-1][0] == who:
            out[-1][1] = (out[-1][1] + " " + text).strip()
        else:
            out.append([who, text])
    return [(who, text) for who, text in out]


def caller_lines(conversation: list[Turn]) -> list[str]:
    return [text for who, text in conversation if who == "caller"]


def last_turn(conversation: list[Turn], who: str) -> str:
    for speaker, text in reversed(conversation):
        if speaker == who:
            return text
    return ""


def _words(text: str) -> list[str]:
    return _PUNCT.sub(" ", (text or "").lower()).split()


def _has_any(text: str, phrases: tuple[str, ...]) -> bool:
    """Whether any phrase appears as whole words in `text`."""
    padded = " " + " ".join(_words(text)) + " "
    return any(f" {phrase} " in padded for phrase in phrases)


def _has_script(text: str, phrases: tuple[str, ...]) -> bool:
    """Urdu-script phrases, matched as written: word boundaries do not
    apply across scripts."""
    return any(phrase in (text or "") for phrase in phrases)


# ---------------------------------------------------------------------------
# Yes and no
# ---------------------------------------------------------------------------

_YES = (
    "ji", "jee", "g", "haan", "han", "ha", "hanji", "haanji", "jihan", "ji haan",
    "haan ji", "yes", "yeah", "yep", "yup", "zaroor", "zarur", "theek", "thik",
    "theek hai", "thik hai", "ok", "okay", "okey", "bilkul", "sahi", "sure",
    "please", "acha", "achha", "accha", "bhej dein", "bhej do", "bhejo", "bhej den",
    "kar dein", "kar do", "kar den", "send it", "go ahead", "of course", "why not",
    "chalega", "correct", "right", "sahi hai", "ji bilkul", "bilkul sahi", "ok ji",
    "yes please", "kardo", "bhejdo", "karein", "kijiye", "kijye", "kar dijiye",
)
_YES_SCRIPT = ("جی", "ہاں", "بالکل", "ضرور", "ٹھیک", "بھیج", "کر دیں", "صحیح")
_NO = (
    "nahi", "nahin", "nai", "nhi", "nah", "no", "nope", "mat", "na", "never",
    "dont", "don't", "not", "no thanks", "koi nahi", "kuch nahi", "nahi chahiye",
    "nahi chahie", "rehne dein", "rehne do", "rehne den", "zaroorat nahi",
    "zarurat nahi", "not needed", "no need", "nahi karna", "mat bhejo",
    "mat bhejein", "galat", "wrong",
)
_NO_SCRIPT = ("نہیں", "نہ ", "مت ", "غلط")


def is_negative(text: str) -> bool:
    return _has_any(text, _NO) or _has_script(text, _NO_SCRIPT)


def is_affirmative(text: str) -> bool:
    """A clear yes, with no no in it."""
    if is_negative(text):
        return False
    return _has_any(text, _YES) or _has_script(text, _YES_SCRIPT)


# ---------------------------------------------------------------------------
# Ending
# ---------------------------------------------------------------------------

_GOODBYE = (
    "allah hafiz", "khuda hafiz", "allah hafez", "khuda hafez", "allahafiz",
    "khudahafiz", "bye", "goodbye", "good bye", "bye bye", "take care",
    "phir milenge", "shab bakhair", "fi amanillah", "ok bye", "thik hai bye",
)
_GOODBYE_SCRIPT = ("اللہ حافظ", "خدا حافظ", "الله حافظ")
_NOTHING_ELSE = (
    "bas", "bus", "koi nahi", "kuch nahi", "nothing else", "nothing more",
    "that's all", "thats all", "that is all", "that's it", "thats it", "no more",
    "aur nahi", "aur kuch nahi", "bas itna", "bas yehi", "itna hi", "bas theek hai",
    "bas thik hai", "done", "that will be all", "no that's all", "nothing",
    "koi aur nahi", "aur koi nahi", "bas ji", "bas shukriya", "enough",
)
_NOTHING_ELSE_SCRIPT = ("بس", "کوئی نہیں", "کچھ نہیں", "اور نہیں")
_THANKS = (
    "shukriya", "shukria", "shukrya", "thank", "thanks", "thankyou", "thank you",
    "jazakallah", "jazak", "mehrbani", "meharbani", "thanku",
)
_THANKS_SCRIPT = ("شکریہ", "مہربانی", "جزاک")
_ANYTHING_ELSE = (
    "anything else", "koi aur", "aur koi", "aur kuch", "kuch aur", "aur sawal",
    "aur kisi", "kisi aur", "any other", "aur madad", "aur help", "koi sawal",
    "else i can", "further", "something else", "anything more", "aur kuch bataun",
    "koi aur sawal", "kisi aur cheez", "aur koi cheez",
)
_ANYTHING_ELSE_SCRIPT = ("اور کوئی", "کوئی اور", "اور کچھ", "کچھ اور")
_QUESTION_WORDS = (
    "kya", "kia", "kitna", "kitni", "kitne", "kab", "kahan", "kahaan", "kaun",
    "kon", "kaise", "kese", "kyun", "kyu", "kis", "konsa", "konsi", "what", "how",
    "when", "where", "which", "who", "why", "can you", "could you", "tell me",
    "bata", "batao", "batayein", "batain", "batayen", "chahiye", "chahie", "want",
    "need", "possible", "ho sakta", "hoga", "milega", "milegi", "please tell",
)


def looks_like_question(text: str) -> bool:
    return "?" in (text or "") or "؟" in (text or "") or _has_any(text, _QUESTION_WORDS)


def is_goodbye(text: str) -> bool:
    return _has_any(text, _GOODBYE) or _has_script(text, _GOODBYE_SCRIPT)


def is_nothing_else(text: str) -> bool:
    return _has_any(text, _NOTHING_ELSE) or _has_script(text, _NOTHING_ELSE_SCRIPT)


def is_thanks(text: str) -> bool:
    return _has_any(text, _THANKS) or _has_script(text, _THANKS_SCRIPT)


def is_closing(text: str) -> bool:
    """A goodbye, a "nothing else", or thanks said with a no: the caller is done."""
    if looks_like_question(text):
        return False
    if is_goodbye(text) or is_nothing_else(text):
        return True
    return is_thanks(text) and is_negative(text)


def asked_anything_else(agent_text: str) -> bool:
    return _has_any(agent_text, _ANYTHING_ELSE) or _has_script(agent_text, _ANYTHING_ELSE_SCRIPT)


def declined(text: str) -> bool:
    """A no, a "nothing else" or a plain thank-you, with no question in it:
    the caller's answer to "anything else?"."""
    if looks_like_question(text):
        return False
    return is_negative(text) or is_nothing_else(text) or is_thanks(text)


def may_end(conversation: list[Turn]) -> bool:
    """Whether the conversation itself says the call may end now.

    (a) the caller's latest turn is a goodbye or a "nothing else", or
    (b) the agent asked whether there was anything else and the caller
        declined.
    """
    if not conversation:
        return False
    last_caller = ""
    agent_before = ""
    seen_caller = False
    for who, text in reversed(conversation):
        if who == "caller" and not seen_caller:
            last_caller = text
            seen_caller = True
            continue
        if who == "agent" and seen_caller:
            agent_before = text
            break
    if not last_caller:
        return False
    if is_closing(last_caller):
        return True
    return asked_anything_else(agent_before) and declined(last_caller)


# ---------------------------------------------------------------------------
# Messaging
# ---------------------------------------------------------------------------

_MESSAGING = (
    "whatsapp", "whats app", "whatsap", "message", "msg", "sms", "likh",
    "in writing", "written", "write", "text", "text me", "send", "bhej", "bhejna",
)
_MESSAGING_SCRIPT = ("واٹس", "میسج", "پیغام", "لکھ", "بھیج")
_CALLER_ASKS = (
    "whatsapp", "whats app", "whatsap", "message", "msg", "sms", "likh", "likh kar",
    "likh ke", "likhkar", "in writing", "written", "text me", "text kar",
    "send me", "bhej do", "bhej dein", "bhej den", "bhejo", "bhej dijiye", "likh do",
    "likh dein", "likh dijiye", "likh kar bhej",
)
_CALLER_ASKS_SCRIPT = ("واٹس", "میسج", "پیغام", "لکھ", "بھیج")
_SAME_NUMBER = (
    "same number", "this number", "calling number", "jis se call", "jisse call",
    "jis say call", "jis se main", "jisse main", "isi number", "issi number",
    "is number", "yehi number", "yahi number", "ye number", "yeh number",
    "isi pe", "isi par", "issi pe", "issi par", "isi pr", "is pe", "is par",
    "isi wale", "same one", "this one", "number i am calling", "number i'm calling",
    "ye wala", "yeh wala", "yehi wala", "isi se", "same", "yehi", "isi",
)
_SAME_NUMBER_SCRIPT = ("اسی نمبر", "اس نمبر", "یہی نمبر", "اسی پر", "اسی پہ", "جس سے")

#: How far back an offer and its yes are looked for: three exchanges.
RECENT_TURNS = 6


def offered_messaging(agent_text: str) -> bool:
    return _has_any(agent_text, _MESSAGING) or _has_script(agent_text, _MESSAGING_SCRIPT)


def caller_asked_for_message(caller_text: str) -> bool:
    return (_has_any(caller_text, _CALLER_ASKS)
            or _has_script(caller_text, _CALLER_ASKS_SCRIPT))


def consented_to_message(conversation: list[Turn]) -> bool:
    """Whether the caller wants a message: they asked for one, or said yes
    to the agent's offer. A no after the latest offer is a no."""
    recent = conversation[-RECENT_TURNS:]
    for who, text in recent:
        if who == "caller" and caller_asked_for_message(text) and not is_negative(text):
            return True
    # The latest offer, and the caller's first answer after it.
    for index in range(len(recent) - 1, -1, -1):
        who, text = recent[index]
        if who == "agent" and offered_messaging(text):
            for later_who, later_text in recent[index + 1:]:
                if later_who == "caller":
                    return is_affirmative(later_text)
            return False
    return False


def said_calling_number(conversation: list[Turn]) -> bool:
    """Whether the caller said to use the number they are calling from."""
    for who, text in conversation[-RECENT_TURNS - 2:]:
        if who == "caller" and (_has_any(text, _SAME_NUMBER)
                                or _has_script(text, _SAME_NUMBER_SCRIPT)):
            return True
    return False


def last_caller_affirmative(conversation: list[Turn]) -> bool:
    return is_affirmative(last_turn(conversation, "caller"))
