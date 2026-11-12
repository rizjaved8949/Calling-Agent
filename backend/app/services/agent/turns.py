"""
Reading the conversation so far, to decide what the agent may do next.

The model says it was asked to send a message, or that the caller is done;
the transcript says whether that is so. These helpers turn the transcript
fragments into turns and answer the small, plain questions the tool guards
ask: did the caller say yes, did they say goodbye, did they ask to end the
call, did the agent offer WhatsApp, did the caller ask for it, did they say
to use the number they are calling from.

Word lists rather than a model, because a guard that calls a model is a guard
that stalls the call. They are deliberately generous with yes and strict with
no: a "nahi" anywhere in the answer wins.

Every line is normalised first (`normalise`), because the live transcription
writes the same words many ways. On two real calls it wrote the caller's Urdu
in Devanagari, and the guard, which knew only Roman Urdu, Urdu script and
English, refused "जी नहीं, बहुत शुक्रिया" four times. So Devanagari is read
letter by letter into Roman, and Roman spellings are folded ("naheen" and
"nahin", "shukriyaa" and "shukriya", "kuchh" and "kuch") before anything is
compared. Urdu script keeps its own lists, matched as written.
"""
from __future__ import annotations

import re

from .script import romanize

Turn = tuple[str, str]

_PUNCT = re.compile(r"[^\w\s']+", re.UNICODE)
_LONG_VOWELS = (("aa", "a"), ("ee", "i"), ("oo", "u"), ("ii", "i"), ("uu", "u"),
                ("hh", "h"), ("ay", "ai"), ("ey", "ai"))


def _fold(text: str) -> str:
    """One spelling for the many the transcription uses, in Roman letters."""
    text = text.lower()
    for long, short in _LONG_VOWELS:
        text = text.replace(long, short)
    # Devanagari नंबर romanises as "nambar"; the lists say "number".
    return text.replace("nambar", "number")


def normalise(text: str) -> str:
    """`text` as the lists are written: Devanagari romanised, Roman folded."""
    return _fold(romanize(text or ""))


def _words(text: str) -> list[str]:
    return _PUNCT.sub(" ", normalise(text)).split()


def _phrases(*items: str) -> tuple[str, ...]:
    """A word list, folded the same way the text will be."""
    return tuple(dict.fromkeys(" ".join(_words(item)) for item in items))


def _has_any(text: str, phrases: tuple[str, ...]) -> bool:
    """Whether any phrase appears as whole words in `text`."""
    padded = " " + " ".join(_words(text)) + " "
    return any(f" {phrase} " in padded for phrase in phrases)


def _has_script(text: str, phrases: tuple[str, ...]) -> bool:
    """Urdu-script phrases, matched as written: word boundaries do not
    apply across scripts."""
    return any(phrase in (text or "") for phrase in phrases)


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


_DIGIT_RUN = re.compile(r"\d{3,}")


def judged(text: str, limit: int = 120) -> str:
    """A caller's words as they may be logged: digits masked, length capped."""
    masked = _DIGIT_RUN.sub("…", text or "")
    return masked if len(masked) <= limit else masked[:limit - 1] + "…"


# ---------------------------------------------------------------------------
# Yes and no
# ---------------------------------------------------------------------------

_YES = _phrases(
    "ji", "jee", "g", "haan", "han", "ha", "hanji", "haanji", "jihan", "ji haan",
    "haan ji", "yes", "yeah", "yep", "yup", "zaroor", "zarur", "theek", "thik", "teek",
    "theek hai", "thik hai", "ok", "okay", "okey", "bilkul", "bilkula", "sahi", "sure",
    "please", "acha", "achha", "accha", "bhej dein", "bhej do", "bhejo", "bhej den",
    "kar dein", "kar do", "kar den", "kar dijiye", "kar dijie", "kar dijiyega",
    "kardijiye", "kardein", "send it", "go ahead", "of course", "why not", "chalega",
    "correct", "right", "sahi hai", "ji bilkul", "bilkul sahi", "ok ji", "yes please",
    "kardo", "bhejdo", "karein", "kijiye", "kijye", "100", "100%", "sau fisad",
    "hundred percent", "yahi", "yehi", "wahi", "exactly", "confirm", "confirmed",
    "theek hai ji", "bilkul theek", "haan ji bilkul", "ji haan bilkul",
)
_YES_SCRIPT = ("جی", "ہاں", "بالکل", "ضرور", "ٹھیک", "بھیج", "کر دیں", "صحیح", "یہی",
               "کر دیجیے", "کر دیجئے")
_NO = _phrases(
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

_GOODBYE = _phrases(
    "allah hafiz", "khuda hafiz", "allah hafez", "khuda hafez", "allahafiz",
    "khudahafiz", "hafiz", "hafij", "hafija", "hafez", "bye", "goodbye", "good bye",
    "bye bye", "take care", "phir milenge", "shab bakhair", "fi amanillah", "ok bye",
    "thik hai bye", "allah hafiz ji", "khuda hafiz ji",
)
_GOODBYE_SCRIPT = ("اللہ حافظ", "خدا حافظ", "الله حافظ", "حافظ")
_NOTHING_ELSE = _phrases(
    "bas", "bus", "koi nahi", "kuch nahi", "nothing else", "nothing more",
    "that's all", "thats all", "that is all", "that's it", "thats it", "no more",
    "aur nahi", "aur kuch nahi", "bas itna", "bas yehi", "itna hi", "bas theek hai",
    "bas thik hai", "done", "that will be all", "nothing", "koi aur nahi",
    "aur koi nahi", "bas ji", "bas shukriya", "enough", "kuch nahi puchna",
    "kuch nahi poochna", "nahi puchna", "nahi poochna", "kuch aur nahi", "mazeed nahi",
    "aur kuch nahi puchna", "kuch nahi puchna chahta", "kuch nahi poochna chahta",
)
_NOTHING_ELSE_SCRIPT = ("بس", "کوئی نہیں", "کچھ نہیں", "اور نہیں", "مزید نہیں")
_THANKS = _phrases(
    "shukriya", "shukria", "shukrya", "thank", "thanks", "thankyou", "thank you",
    "jazakallah", "jazak", "mehrbani", "meharbani", "thanku", "bahut shukriya",
    "bohat shukriya", "bohot shukriya",
)
_THANKS_SCRIPT = ("شکریہ", "مہربانی", "جزاک")
#: The caller asking for the call to end, including what the transcription
#: makes of "call end" on a Pakistani line: "kol end", "call and".
_END_CALL = _phrases(
    "call end", "kol end", "col end", "call and", "kol and", "end kar", "and kar",
    "end karo", "end karen", "end kardo", "end kar do", "end kar den", "end kar dein",
    "band kar", "band karo", "band karen", "band kardo", "khatam kar", "khatm kar",
    "cut kar", "cut karo", "cut kardo", "rakh dein", "rakh do", "rakh den", "rakhdo",
    "phone rakh", "phone band", "hang up", "end the call", "end call", "disconnect",
    "call khatam", "call band", "call cut", "call rakh", "end it", "end this call",
    "call ko end", "call ko band", "kol ko end", "kol band",
)
_END_CALL_SCRIPT = ("کال بند", "بند کر", "ختم کر", "کال ختم", "رکھ دیں", "کاٹ دیں",
                    "کال اینڈ", "اینڈ کر", "کال کاٹ")
#: The agent's own closing line: thanks for calling, and the goodbye.
_CLOSING_LINE = _phrases(
    "call karne ka shukriya", "call karne ke liye shukriya", "thank you for calling",
    "thanks for calling", "rabta karne ka shukriya", "contact karne ka shukriya",
    "call karne ka bohat shukriya",
)
_ANYTHING_ELSE = _phrases(
    "anything else", "koi aur", "aur koi", "aur kuch", "kuch aur", "aur sawal",
    "aur kisi", "kisi aur", "any other", "aur madad", "aur help", "koi sawal",
    "else i can", "further", "something else", "anything more", "aur kuch bataun",
    "koi aur sawal", "kisi aur cheez", "aur koi cheez", "mazeed", "iske alawa",
    "is ke alawa", "koi aur baat",
)
_ANYTHING_ELSE_SCRIPT = ("اور کوئی", "کوئی اور", "اور کچھ", "کچھ اور", "مزید", "اس کے علاوہ")
#: Words that make a line a question or a request in themselves, in the
#: languages of these calls. A line with none of these, and none of the
#: topic words below, is not a new request, whatever punctuation the
#: transcription hung on it: "And that's things?" was background noise, and
#: it kept a call open.
_QUESTION_WORDS = _phrases(
    "kya", "kia", "kitna", "kitni", "kitne", "kab", "kahan", "kahaan", "kaun",
    "kon", "kaise", "kese", "kyun", "kyu", "kis", "konsa", "konsi", "what", "how",
    "when", "where", "which", "who", "why", "can you", "could you", "tell me",
    "bata", "batao", "batayein", "batain", "batayen", "chahiye", "chahie", "want",
    "need", "possible", "ho sakta", "hoga", "milega", "milegi", "please tell",
    "bata dein", "bata do", "batana", "chahta", "chahti", "karna hai", "karni hai",
    "karun", "karoon", "sakte hain", "sakti hain", "sakta hoon", "sakti hoon",
)
_QUESTION_SCRIPT = ("کیا", "کتنا", "کتنی", "کب", "کہاں", "کون", "کیسے", "کیوں", "بتائیں",
                    "چاہیے")
#: Things people ring about. One of these makes a line a request unless the
#: line is a thank-you or a goodbye: "thanks for the details, bye" is not
#: asking for details.
_TOPIC_WORDS = _phrases(
    "price", "qeemat", "keemat", "rate", "range", "cost", "fee", "fees", "detail",
    "details", "information", "maloomat", "jankari", "book", "booking", "test drive",
    "test draiw", "timing", "address", "location", "model", "models", "hua", "help",
    "madad", "problem", "masla", "complaint", "kamplent", "shikayat", "warranty",
    "service", "charging", "charger", "battery", "delivery", "colour", "color",
)
_TOPIC_SCRIPT = ("قیمت", "ریٹ", "معلومات", "بکنگ", "مدد", "مسئلہ", "شکایت")


def is_new_request(text: str) -> bool:
    """Whether the caller's line asks for something, as opposed to noise.

    Two words or more, with a question word, or a topic word outside a
    thank-you or goodbye. Not a question mark: the transcription hangs those
    on noise and on sentences in other languages ("Muito bela essa
    maquiagem que ti, o que aconteceu?").
    """
    if len(_words(text)) < 2:
        return False
    if _has_any(text, _QUESTION_WORDS) or _has_script(text, _QUESTION_SCRIPT):
        return True
    if not (_has_any(text, _TOPIC_WORDS) or _has_script(text, _TOPIC_SCRIPT)):
        return False
    return not (is_goodbye(text) or is_thanks(text) or is_nothing_else(text))


def looks_like_question(text: str) -> bool:
    return is_new_request(text)


def is_meaningful(text: str) -> bool:
    """A caller line that says something the guard can act on: an answer, a
    goodbye, a request. Anything else is noise and is skipped."""
    return (is_new_request(text) or is_closing(text) or is_affirmative(text)
            or is_negative(text) or is_thanks(text))


def agent_asked(agent_text: str) -> bool:
    """The agent's turn put a question to the caller."""
    return "?" in (agent_text or "") or "؟" in (agent_text or "") or asked_anything_else(agent_text)


def is_goodbye(text: str) -> bool:
    return _has_any(text, _GOODBYE) or _has_script(text, _GOODBYE_SCRIPT)


def is_nothing_else(text: str) -> bool:
    return _has_any(text, _NOTHING_ELSE) or _has_script(text, _NOTHING_ELSE_SCRIPT)


def is_thanks(text: str) -> bool:
    return _has_any(text, _THANKS) or _has_script(text, _THANKS_SCRIPT)


def asked_to_end(text: str) -> bool:
    """The caller asking for the call to end, in any of its forms."""
    return _has_any(text, _END_CALL) or _has_script(text, _END_CALL_SCRIPT)


def is_closing(text: str) -> bool:
    """A goodbye, a "nothing else", thanks said with a no, or asking to end:
    the caller is done. Asking to end wins even when phrased as a question."""
    if asked_to_end(text) or is_goodbye(text):
        return True
    if looks_like_question(text):
        return False
    if is_nothing_else(text):
        return True
    return is_thanks(text) and is_negative(text)


def asked_anything_else(agent_text: str) -> bool:
    return _has_any(agent_text, _ANYTHING_ELSE) or _has_script(agent_text, _ANYTHING_ELSE_SCRIPT)


def agent_said_closing(agent_text: str) -> bool:
    """The agent's closing line: a goodbye, or thanks for calling."""
    return is_goodbye(agent_text) or _has_any(agent_text, _CLOSING_LINE)


def declined(text: str) -> bool:
    """A no, a "nothing else" or a plain thank-you, with no question in it:
    the caller's answer to "anything else?"."""
    if looks_like_question(text):
        return False
    return is_negative(text) or is_nothing_else(text) or is_thanks(text)


def last_meaningful_caller_turn(conversation: list[Turn]) -> tuple[int, str]:
    """(index, text) of the caller's last line that says something, or (-1, "")."""
    for index in range(len(conversation) - 1, -1, -1):
        who, text = conversation[index]
        if who == "caller" and is_meaningful(text):
            return index, text
    return -1, ""


def may_end(conversation: list[Turn]) -> bool:
    """Whether the conversation itself says the call may end now.

    Judged on the caller's last *meaningful* line, skipping noise: on a real
    call the goodbye was followed by a mis-transcribed "And that's things?",
    and the guard refused on that.

    (a) that line is a goodbye, a "nothing else", or asks to end the call, or
    (b) the agent's last real question was answered with a no, a "nothing
        else" or a goodbye, or
    (c) the agent has said its closing line, and the caller has asked
        nothing new since.
    """
    if not conversation:
        return False
    index, last_caller = last_meaningful_caller_turn(conversation)
    agent_before = next((t for w, t in reversed(conversation[:index]) if w == "agent"), "") \
        if index >= 0 else ""
    if last_caller and is_closing(last_caller):
        return True
    if last_caller and agent_asked(agent_before) and (declined(last_caller)
                                                      or is_goodbye(last_caller)):
        return True
    # (c): after the agent's goodbye, nothing new has been asked. A goodbye
    # said in the same breath as the answer to a question does not count:
    # the caller has not had a chance to say whether they are done.
    last_agent_index = next((i for i in range(len(conversation) - 1, -1, -1)
                             if conversation[i][0] == "agent"), -1)
    if last_agent_index >= 0 and agent_said_closing(conversation[last_agent_index][1]):
        asked_since = any(w == "caller" and is_new_request(t)
                          for w, t in conversation[last_agent_index + 1:])
        asked_before = index >= 0 and index < last_agent_index and is_new_request(last_caller)
        if not asked_since and not asked_before:
            return True
    return False


# ---------------------------------------------------------------------------
# Messaging
# ---------------------------------------------------------------------------

_MESSAGING = _phrases(
    "whatsapp", "whats app", "whatsap", "message", "msg", "sms", "likh",
    "in writing", "written", "write", "text", "text me", "send", "bhej", "bhejna",
)
_MESSAGING_SCRIPT = ("واٹس", "میسج", "پیغام", "لکھ", "بھیج")
_CALLER_ASKS = _phrases(
    "whatsapp", "whats app", "whatsap", "message", "msg", "sms", "likh", "likh kar",
    "likh ke", "likhkar", "in writing", "written", "text me", "text kar",
    "send me", "bhej do", "bhej dein", "bhej den", "bhejo", "bhej dijiye", "likh do",
    "likh dein", "likh dijiye", "likh kar bhej", "send kar", "send kar dein",
)
_CALLER_ASKS_SCRIPT = ("واٹس", "میسج", "پیغام", "لکھ", "بھیج")
_SAME_NUMBER = _phrases(
    "same number", "this number", "calling number", "jis se call", "jisse call",
    "jis say call", "jis se main", "jisse main", "isi number", "issi number",
    "is number", "yehi number", "yahi number", "ye number", "yeh number",
    "isi pe", "isi par", "issi pe", "issi par", "isi pr", "is pe", "is par",
    "isi wale", "same one", "this one", "number i am calling", "number i'm calling",
    "ye wala", "yeh wala", "yehi wala", "isi se", "same", "yehi", "isi",
    "jis par aap call", "jis par baat", "jis se baat", "yeh wala number",
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


#: Unmistakably "the number I am calling from": the number itself named,
#: not a bare "wahi" or "same" that may mean the one given earlier.
_CALLING_CHOICE = _phrases(
    "isi number", "issi number", "is number", "yehi number", "yahi number", "ye number",
    "yeh number", "same number", "this number", "calling number", "jis se call",
    "jisse call", "jis say call", "jis se main", "jisse main", "jis par aap call",
    "jis par baat", "jis se baat", "number i am calling", "number i'm calling",
    "yeh wala number", "ye wala number", "isi pe", "isi par", "issi pe", "issi par",
    "is pe", "is par", "isi se", "isi wale", "jis number se call", "jis se baat ho rahi",
    "the number i am on", "number i'm on", "jis pe call", "jis pe baat", "jis number se",
    "jis number par", "jis number pe", "is number se",
)
_CALLING_CHOICE_SCRIPT = ("اسی نمبر", "اس نمبر", "یہی نمبر", "اسی پر", "اسی پہ", "جس سے")
#: "That number", "the one I gave before": the number dictated earlier.
_EARLIER_CHOICE = _phrases(
    "wahi number", "wohi number", "us number", "pehle wala", "pehla number", "pehle wale",
    "that number", "the number i gave", "jo number maine", "jo maine diya", "jo maine bataya",
    "same as before", "wahi jo", "wo number", "woh number", "pehle diya", "pehle bataya",
    "jo number bataya", "jo number diya", "the one i gave", "the earlier number",
)
_EARLIER_CHOICE_SCRIPT = ("وہی نمبر", "پہلے والا", "پہلا نمبر", "جو نمبر میں نے")


def mentions_calling_number(text: str) -> bool:
    """This turn names the number the caller is calling from."""
    return _has_any(text, _CALLING_CHOICE) or _has_script(text, _CALLING_CHOICE_SCRIPT)


def mentions_earlier_number(text: str) -> bool:
    """This turn points back at a number the caller gave before."""
    return _has_any(text, _EARLIER_CHOICE) or _has_script(text, _EARLIER_CHOICE_SCRIPT)


def last_caller_affirmative(conversation: list[Turn]) -> bool:
    return is_affirmative(last_turn(conversation, "caller"))
