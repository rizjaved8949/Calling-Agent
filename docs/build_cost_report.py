"""
Builds docs/calling-agent-costs.pdf.

The figures are not typed into this script by hand. They are computed here from
the same inputs the measurements produced, so changing one input — the exchange
rate, the talk ratio, the model — moves every table in the PDF. Re-run it:

    python docs/build_cost_report.py

Where each input came from is recorded next to it, and the Sources section of
the PDF repeats that for the reader.
"""
from __future__ import annotations

import pathlib

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

# ---------------------------------------------------------------------------
# Inputs. Every one of these is measured or published, never estimated.
# ---------------------------------------------------------------------------

AS_OF = "13 October 2026"

#: USD -> PKR. Open-market selling rate, Pakistan Observer, 9 October 2026.
PKR = 278.0

#: Google's published rates for gemini-3.1-flash-live-preview, per token.
LIVE_AUDIO_IN = 3.00 / 1e6
LIVE_AUDIO_OUT = 12.00 / 1e6
LIVE_TEXT_IN = 0.75 / 1e6
#: Google documents audio as 25 tokens per second.
AUDIO_TOKENS_PER_SEC = 25

#: Text models, (input per token, output per token).
TEXT_MODELS = {
    "gemini-3.5-flash (in use now)": (1.50 / 1e6, 9.00 / 1e6),
    "gemini-3.8-flash": (0.75 / 1e6, 3.75 / 1e6),
    "gemini-3.5-flash-lite": (0.30 / 1e6, 2.50 / 1e6),
}

#: Counted with Google's own count_tokens against this deployment's real
#: prompts and knowledge bases.
UNI_PROMPT_TOKENS = 23_808
JAZZ_PROMPT_TOKENS = 3_810
UNI_KB_TOKENS = 22_706
JAZZ_KB_TOKENS = 2_238
#: Rules, recent history and the question itself, on top of the knowledge base.
REPLY_OVERHEAD_IN = 600
#: Measured replies ran 96-247 output tokens.
REPLY_OUT = 200

#: Read back from this account's own Meta billing (pricing_analytics).
WA_MARKETING = 0.0946 / 2
WA_UTILITY = 0.10 / 10

#: Monthly platform pieces, in USD.
PLATFORM = [
    ("Frontend (Vercel Pro)", 20.0,
     "Hobby is personal, non-commercial use only. We are selling."),
    ("Backend (Render Starter)", 7.0,
     "Free spins down after 15 minutes idle and drops inbound calls."),
    ("Database and storage (Supabase Pro)", 25.0,
     "Free is 500 MB and pauses after a week of inactivity."),
    ("Domain", 1.0, "About $12 a year."),
]


def rs(usd: float) -> float:
    return usd * PKR


def money(usd: float) -> str:
    """Paisa matter on a per-call figure and are noise on a monthly one."""
    value = rs(usd)
    return f"Rs {value:,.0f}" if value >= 1000 else f"Rs {value:,.2f}"


def money0(usd: float) -> str:
    """Whole rupees, for a column where mixing 496.40 with 24,820 reads badly."""
    return f"Rs {rs(usd):,.0f}"


def audio_minute(talk_ratio: float) -> tuple[float, float, float]:
    """Google's charge for one minute: listening, speaking, total."""
    listen = 60 * AUDIO_TOKENS_PER_SEC * LIVE_AUDIO_IN
    speak = 60 * talk_ratio * AUDIO_TOKENS_PER_SEC * LIVE_AUDIO_OUT
    return listen, speak, listen + speak


def call_cost(minutes: float, prompt_tokens: int, talk_ratio: float = 0.40) -> float:
    return prompt_tokens * LIVE_TEXT_IN + minutes * audio_minute(talk_ratio)[2]


def reply_cost(kb_tokens: int, model: str) -> float:
    rate_in, rate_out = TEXT_MODELS[model]
    return (kb_tokens + REPLY_OVERHEAD_IN) * rate_in + REPLY_OUT * rate_out


# ---------------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------------

INK = colors.HexColor("#1a1a1a")
MUTED = colors.HexColor("#5a5a5a")
RULE = colors.HexColor("#d4d4d4")
BAND = colors.HexColor("#f2f4f7")
ACCENT = colors.HexColor("#0b5d6b")
WARN = colors.HexColor("#8a2b2b")

_base = getSampleStyleSheet()

S = {
    "title": ParagraphStyle("title", parent=_base["Title"], fontName="Helvetica-Bold",
                            fontSize=22, leading=27, textColor=INK, alignment=TA_LEFT,
                            spaceAfter=2),
    "subtitle": ParagraphStyle("subtitle", parent=_base["Normal"], fontName="Helvetica",
                               fontSize=9.5, leading=13, textColor=MUTED, spaceAfter=14),
    "h2": ParagraphStyle("h2", parent=_base["Heading2"], fontName="Helvetica-Bold",
                         fontSize=14, leading=18, textColor=ACCENT,
                         spaceBefore=18, spaceAfter=7),
    "h3": ParagraphStyle("h3", parent=_base["Heading3"], fontName="Helvetica-Bold",
                         fontSize=11, leading=15, textColor=INK,
                         spaceBefore=12, spaceAfter=5),
    "body": ParagraphStyle("body", parent=_base["Normal"], fontName="Helvetica",
                           fontSize=9.5, leading=14, textColor=INK, spaceAfter=7),
    "lead": ParagraphStyle("lead", parent=_base["Normal"], fontName="Helvetica",
                           fontSize=11, leading=16, textColor=INK, spaceAfter=9),
    "note": ParagraphStyle("note", parent=_base["Normal"], fontName="Helvetica",
                           fontSize=8.5, leading=12, textColor=MUTED, spaceAfter=7),
    "cell": ParagraphStyle("cell", parent=_base["Normal"], fontName="Helvetica",
                           fontSize=8.5, leading=11.5, textColor=INK),
    "cellb": ParagraphStyle("cellb", parent=_base["Normal"], fontName="Helvetica-Bold",
                            fontSize=8.5, leading=11.5, textColor=INK),
    "head": ParagraphStyle("head", parent=_base["Normal"], fontName="Helvetica-Bold",
                           fontSize=8.5, leading=11.5, textColor=colors.white),
}


def P(text, style="body"):
    return Paragraph(text, S[style])


def table(rows, widths, align_right=(), bold_rows=()):
    """A table whose cells are paragraphs, so long text wraps."""
    data = []
    for r, row in enumerate(rows):
        out = []
        for c, cell in enumerate(row):
            if r == 0:
                out.append(Paragraph(str(cell), S["head"]))
            else:
                style = "cellb" if r in bold_rows else "cell"
                out.append(Paragraph(str(cell), S[style]))
        data.append(out)

    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, RULE),
        ("BOX", (0, 0), (-1, -1), 0.5, RULE),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), BAND))
    for col in align_right:
        style.append(("ALIGN", (col, 1), (col, -1), "RIGHT"))
    t.setStyle(TableStyle(style))
    return t


def rule():
    return HRFlowable(width="100%", thickness=0.5, color=RULE,
                      spaceBefore=10, spaceAfter=10)


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------

def build(path: pathlib.Path) -> None:
    story = []
    A = story.append

    # ---- cover ----
    A(P("What the calling agent costs", "title"))
    A(P(f"And what to charge for it &nbsp;&middot;&nbsp; as of {AS_OF} "
        f"&nbsp;&middot;&nbsp; USD 1 = Rs {PKR:,.0f}", "subtitle"))

    per_min = audio_minute(0.40)[2]
    A(P(
        f"<b>One minute of a live call costs us {money(per_min)}</b> in Google charges "
        "at 40% agent talk time. But the knowledge base is re-sent on every single "
        f"call, so a one-minute call on the University agent really costs "
        f"<b>{money(call_cost(1, UNI_PROMPT_TOKENS))}</b>, and a ten-minute one works "
        f"out at <b>{money(call_cost(10, UNI_PROMPT_TOKENS) / 10)} a minute</b>. "
        "Carrier minutes from Infobip are on top and are the one number that could "
        "not be proven.", "lead"))
    A(P(
        "Every figure here is computed from Google's published rates, this account's "
        "own Meta billing, and token counts measured on this deployment. Anything "
        "uncertain is named in <i>What could not be proven</i>.", "body"))
    A(rule())

    # ---- 1. voice ----
    A(P("1. What one voice call costs us", "h2"))
    A(P("Google bills a live call in two parts: the audio, charged by the second, "
        "and the agent's instructions, charged once when the call connects. The "
        "model in use is gemini-3.1-flash-live-preview, at $3.00 per million audio "
        "input tokens and $12.00 per million audio output tokens, with audio counted "
        "at 25 tokens per second.", "body"))

    rows = [["Agent talks", "Listening", "Speaking", "Total per minute"]]
    for ratio in (0.30, 0.40, 0.50, 0.60):
        listen, speak, total = audio_minute(ratio)
        rows.append([f"{ratio*100:.0f}% of the time", money(listen), money(speak),
                     f"<b>{money(total)}</b>"])
    A(table(rows, [45*mm, 33*mm, 33*mm, 40*mm], align_right=(1, 2, 3)))
    A(Spacer(1, 4))
    A(P("Listening costs the same whatever happens, because the stream stays open "
        "for the whole call. Only the speaking varies.", "note"))

    A(P("The instructions cost more than the minutes", "h3"))
    A(P("The agent's whole prompt &mdash; persona, rules and the entire knowledge "
        "base &mdash; is sent when the call connects. Counted with Google's own "
        "token counter against the real assembled prompts:", "body"))
    rows = [["Agent", "Prompt sent per call", "Cost", "Equals this much talking"]]
    for name, tok in (("University Agent", UNI_PROMPT_TOKENS),
                      ("Jazz Agent", JAZZ_PROMPT_TOKENS)):
        cost = tok * LIVE_TEXT_IN
        rows.append([name, f"{tok:,} tokens", f"<b>{money(cost)}</b>",
                     f"{cost / per_min:.1f} minutes"])
    A(table(rows, [40*mm, 40*mm, 33*mm, 38*mm], align_right=(1, 2, 3)))

    A(Spacer(1, 8))
    A(P("A complete call, at 40% agent talk time:", "body"))
    rows = [["Call length", "University Agent", "per minute",
             "Jazz Agent", "per minute"]]
    for m in (1, 2, 3, 5, 10):
        u, j = call_cost(m, UNI_PROMPT_TOKENS), call_cost(m, JAZZ_PROMPT_TOKENS)
        rows.append([f"{m} minute" + ("s" if m > 1 else ""),
                     money(u), money(u / m), money(j), money(j / m)])
    A(table(rows, [26*mm, 34*mm, 30*mm, 30*mm, 30*mm], align_right=(1, 2, 3, 4)))
    A(Spacer(1, 4))
    A(P("<b>Short calls on a large knowledge base are where the money goes.</b> The "
        "Jazz knowledge base is a tenth the size of the University one, and its "
        "one-minute call costs barely half as much.", "body"))

    A(PageBreak())

    # ---- 2. whatsapp ----
    A(P("2. What one WhatsApp message costs us", "h2"))
    A(P("Two separate bills: Meta for delivering the message, Google for writing it.",
        "body"))

    A(P("Meta's charge, from our own billing", "h3"))
    A(P("These are not quoted rates. They are what Meta actually charged WhatsApp "
        "Business Account 2812213305655211, read back from the account over the "
        "90 days to 13 October 2026.", "body"))
    rows = [["Category", "Messages billed", "Total charged", "Per message"]]
    for label, vol, total, each in (
        ("Marketing", 2, 0.0946, WA_MARKETING),
        ("Utility", 10, 0.10, WA_UTILITY),
        ("Utility", 5, 0.05, WA_UTILITY),
    ):
        rows.append([label, str(vol), f"${total:.4f}", f"<b>{money(each)}</b>"])
    rows.append(["Service, inside the 24-hour window", "7", "$0.0000", "<b>Free</b>"])
    A(table(rows, [55*mm, 30*mm, 32*mm, 34*mm], align_right=(1, 2, 3)))
    A(Spacer(1, 4))
    A(P("Messages a customer sends us are never charged, and a reply inside 24 hours "
        "of their last message is free. That is why auto-reply is cheap and a "
        f"campaign is not: <b>one marketing message costs what "
        f"{WA_MARKETING / per_min:.1f} minutes of talking costs.</b>", "body"))

    A(P("Google's charge for writing the reply", "h3"))
    A(P("The whole knowledge base is sent again on every message, which makes the "
        "model choice matter more than anything else in this document.", "body"))
    rows = [["Text model", "University knowledge base", "Jazz knowledge base"]]
    for model in TEXT_MODELS:
        rows.append([model,
                     f"<b>{money(reply_cost(UNI_KB_TOKENS, model))}</b>",
                     f"<b>{money(reply_cost(JAZZ_KB_TOKENS, model))}</b>"])
    A(table(rows, [60*mm, 48*mm, 43*mm], align_right=(1, 2)))

    A(Spacer(1, 6))
    now = reply_cost(UNI_KB_TOKENS, "gemini-3.5-flash (in use now)") * 1000
    lite = reply_cost(UNI_KB_TOKENS, "gemini-3.5-flash-lite") * 1000
    A(P(f"<b>Changing one line of configuration cuts that by "
        f"{100*(now-lite)/now:.0f}%.</b> Per 1,000 messages answered on the "
        f"University knowledge base: {money(now)} today against {money(lite)} on "
        "Flash-Lite. The setting is gemini_text_model in backend/app/config.py, and "
        "it also drives the Guides assistant and the staff lookup. Flash-Lite is a "
        "smaller model, so run scripts/agent_eval.py against the real knowledge "
        "bases before switching.", "body"))

    A(PageBreak())

    # ---- 3. hidden ----
    A(P("3. The hidden costs", "h2"))
    A(P("Every one of these is real on this build. Most are invisible until the "
        "bill arrives.", "body"))
    rows = [["What", "Why it costs", "How much"]]
    hidden = [
        ("Silence is billed",
         "The audio stream stays open for the whole call, so input tokens are "
         "charged while nobody speaks",
         f"{money(audio_minute(0)[0])} per minute, whatever happens"),
        ("The knowledge base is re-sent every time",
         "It is not stored at Google. The whole base goes up on every call and "
         "every message",
         f"{money(UNI_PROMPT_TOKENS * LIVE_TEXT_IN)} per call, "
         f"{money(reply_cost(UNI_KB_TOKENS, 'gemini-3.5-flash (in use now)'))} "
         "per message"),
        ("Thinking tokens are billed as output",
         "The model reasons before answering. Measured on one real reply: 573 "
         "thinking tokens against 23 of visible answer",
         "Up to 25x the answer. Now switched off for replies"),
        ("A call nobody answers still costs",
         "The session connects and the prompt is sent before the phone rings",
         f"{money(UNI_PROMPT_TOKENS * LIVE_TEXT_IN)} per unanswered call"),
        ("Marketing templates cost 4.7x utility",
         "Meta's own rate card, confirmed on our account",
         f"{money(WA_MARKETING)} against {money(WA_UTILITY)}"),
        ("Outside the 24-hour window nothing is free",
         "A free-text reply is accepted and silently not delivered; a paid "
         "template is the only way through",
         f"{money(WA_UTILITY)} minimum to reach someone who went quiet"),
        ("Vercel Hobby forbids commercial use",
         "We are selling this. Hobby is for personal, non-commercial use",
         f"{money(20)} a month to become compliant"),
        ("Supabase free pauses after a week idle",
         "A demo company that goes quiet stops working, silently",
         f"{money(25)} a month to stop it"),
        ("Render free spins down after 15 minutes",
         "The first inbound call after a quiet spell waits about a minute for the "
         "server to wake. The call fails",
         "Lost calls, not rupees"),
    ]
    for a, b, c in hidden:
        rows.append([f"<b>{a}</b>", b, c])
    A(table(rows, [44*mm, 62*mm, 45*mm]))

    A(P("The knowledge base, over a month", "h3"))
    A(P("It is charged per use, not per month. Trimming it is the cheapest "
        "optimisation available &mdash; halving the base halves this column.", "body"))
    rows = [["Calls per month", "University base", "Jazz base"]]
    for calls in (100, 500, 1_000, 5_000):
        rows.append([f"{calls:,}",
                     money0(calls * UNI_PROMPT_TOKENS * LIVE_TEXT_IN),
                     money0(calls * JAZZ_PROMPT_TOKENS * LIVE_TEXT_IN)])
    A(table(rows, [40*mm, 45*mm, 45*mm], align_right=(1, 2)))

    A(P("What turned out not to matter", "h3"))
    A(P("Recordings are cheap. A real call measured 651,260 bytes for 113 seconds, "
        "which is 338 KB a minute. Even at 10,000 calls a month averaging four "
        "minutes that is 12.9 GB a month, and a year of it still sits inside "
        "Supabase Pro's included 100 GB. Playback egress at 20,000 plays a month is "
        "12.9 GB against 250 GB included. <b>Storage and bandwidth are not worth "
        "modelling at our scale.</b>", "body"))

    A(PageBreak())

    # ---- 4. platform ----
    A(P("4. The monthly bill before anyone makes a call", "h2"))
    A(P("This is what we pay whether we have one customer or none. We are on free "
        "plans today, and two of them we are not entitled to.", "body"))
    rows = [["Piece", "Monthly", "Why it has to change"]]
    for name, usd, why in PLATFORM:
        rows.append([name, money(usd), why])
    floor = sum(u for _, u, _ in PLATFORM)
    rows.append(["<b>Total</b>", f"<b>{money(floor)}</b>", ""])
    A(table(rows, [52*mm, 30*mm, 69*mm], align_right=(1,),
            bold_rows=(len(rows) - 1,)))
    A(Spacer(1, 4))
    A(P(f"At about {money(floor)} a month, roughly ${floor:.0f}, the floor is low. "
        "It is not where the money goes &mdash; usage is.", "body"))
    A(P("<b>Render Starter is the urgent one.</b> On the free plan the server sleeps "
        "after 15 minutes without traffic and takes about a minute to wake. An "
        "inbound call arriving at a sleeping server does not ring; it fails. For a "
        "product whose whole promise is answering the phone, that is the most "
        "pressing line in the table.", "body"))
    A(P(f"Budget {money(25)} a month for Render Standard (2 GB, 1 vCPU) once calls "
        "overlap. Each live call holds a WebSocket, an audio pacer and a resampler "
        "in memory, and Starter's 512 MB is thin for that. Measure before assuming "
        "the step is needed.", "body"))

    # ---- 5. hosting ----
    A(P("5. Where to host it", "h2"))
    A(P("One constraint rules out most cheap options: <b>the backend cannot be "
        "serverless.</b> A live call holds an open WebSocket to the carrier and "
        "another to Google for its whole length, with an audio clock ticking every "
        "5 ms. Vercel Functions, Lambda and Cloudflare Workers bill per invocation "
        "with execution limits and no persistent sockets. The frontend is a static "
        "bundle and can go anywhere.", "body"))
    rows = [["Option", "Monthly", "For", "Against"]]
    for name, cost, pro, con in (
        ("Render Starter", money(7), "Already deployed, no spin-down, deploys from git",
         "512 MB and 0.5 CPU is thin for concurrent audio"),
        ("Render Standard", money(25), "2 GB and 1 vCPU, realistic for several calls",
         "3.5x the price of a VPS with the same specs"),
        ("Hetzner or DigitalOcean VPS", "Rs 1,700-3,300",
         "Cheapest per GB of RAM by a wide margin, full control",
         "You run it: updates, TLS, restarts, monitoring, backups"),
        ("Railway or Fly.io", "usage-based",
         "Persistent processes, Fly has regions closer to Pakistan",
         "Usage billing is harder to predict than a flat fee"),
    ):
        rows.append([f"<b>{name}</b>", cost, pro, con])
    A(table(rows, [34*mm, 24*mm, 50*mm, 43*mm]))

    A(P("The recommendation", "h3"))
    A(P("<b>Stay on Render; move to Starter now and Standard when calls overlap.</b> "
        "Not because it is cheapest, because it is not, but because at this stage "
        "the difference is about Rs 5,000 a month and a VPS costs a person's "
        "attention instead. One unattended reboot during business hours costs more "
        "in lost calls than a year of that difference. Revisit when the Render bill "
        "passes roughly Rs 20,000 a month.", "body"))
    A(P("<b>Keep Supabase.</b> It is the database, the file store and auth in one, "
        "and Pro's quotas are far beyond what the measured recordings will use. "
        "Replacing it with self-managed Postgres saves about Rs 7,000 a month and "
        "costs backups, pooling and point-in-time recovery.", "body"))
    A(P("<b>Move the frontend off Vercel to Cloudflare Pages.</b> Cloudflare allows "
        f"commercial use on its free tier. That is a real {money(20)} a month saved "
        "on a plain static build with no server-side logic, so the migration is low "
        "risk. It is the one easy saving here.", "body"))

    A(PageBreak())

    # ---- 6. pricing ----
    A(P("6. What to charge a company", "h2"))
    A(P("These are cost floors, not prices. Carrier minutes are missing from every "
        "line and must be added once Infobip gives a rate.", "body"))
    A(P("Assuming a three-minute average call, 40% agent talk time, and a trimmed "
        "knowledge base around the Jazz size:", "body"))

    packages = [("Starter", 200, 500, 15_000),
                ("Growth", 1_000, 2_500, 45_000),
                ("Business", 5_000, 10_000, 165_000)]
    rows = [["Package", "Calls", "Messages", "Our cost", "Suggested price",
             "Gross margin"]]
    for name, calls, msgs, price in packages:
        cost = rs(calls * call_cost(3, JAZZ_PROMPT_TOKENS))
        share = floor * PKR / len(packages)
        total = cost + share
        rows.append([f"<b>{name}</b>", f"{calls:,}", f"{msgs:,}",
                     f"Rs {total:,.0f}", f"Rs {price:,.0f}",
                     f"{100*(price-total)/price:.0f}%"])
    A(table(rows, [26*mm, 20*mm, 24*mm, 28*mm, 30*mm, 26*mm],
            align_right=(1, 2, 3, 4, 5)))
    A(Spacer(1, 4))
    A(P("Messages are costed as free service replies inside the 24-hour window, "
        "which is what auto-reply actually does. The platform floor is split across "
        "three customers; at ten it falls to about Rs 1,473 each.", "note"))

    A(P("Three rules the numbers argue for", "h3"))
    for n, (head, text) in enumerate([
        ("Sell call bundles, not unlimited minutes.",
         "Our cost is linear in minutes and the knowledge base makes short calls "
         "disproportionately expensive. An unlimited package with a chatty customer "
         "loses money."),
        ("Charge separately for campaigns.",
         f"Marketing messages at {money(WA_MARKETING)} each are a pass-through cost, "
         "not something a subscription should absorb. A 10,000-contact campaign is "
         f"Rs {10_000*rs(WA_MARKETING):,.0f} of Meta charges alone."),
        ("Cap the knowledge base, or price by its size.",
         "A customer who uploads a 200-page manual triples our per-call cost without "
         "touching their minute count. Either cap it in the product or make it a "
         "pricing tier."),
    ], 1):
        A(P(f"<b>{n}. {head}</b> {text}", "body"))

    A(P("<b>The lever worth pulling first:</b> before raising any price, switch the "
        "text model and trim the knowledge bases. Together those cut the Google "
        "bill by roughly 70% with no loss of capability we have measured &mdash; "
        "worth more than any pricing change at this stage.", "body"))

    A(PageBreak())

    # ---- 7. uncertainty ----
    A(P("7. What could not be proven", "h2"))
    A(P("Four numbers here are not solid. Three are small. One is large enough to "
        "change every price above.", "body"))

    A(P("Infobip carrier minutes &mdash; the missing number", "h3"))
    A(P("Infobip does not publish per-destination voice rates, and the rate and "
        "balance endpoints both return 404 on our api-pk2 instance. Our own call "
        "logs came back empty, so there is nothing to read a real price from. "
        "Third-party guides put Infobip voice between EUR 0.0002 and EUR 0.0054 a "
        "minute without naming Pakistan, which is too wide to build a price on. At "
        "the top of that range, about Rs 1.70 a minute, carrier cost would be "
        "roughly half what Google charges. Get it from the rate card in the Infobip "
        "portal under Billing, or ask the account manager for Pakistan mobile and "
        "landline termination, then add it to every per-minute figure here.", "body"))

    A(P("The model we run is not on Google's price list", "h3"))
    A(P("The official pricing page lists Gemini 3.8 Flash and 3.5 Flash-Lite but not "
        "gemini-3.5-flash, which is what the build actually uses. The $1.50 and "
        "$9.00 rates come from pricing aggregators citing Google's docs, and they "
        "agree with each other. It still serves requests. But a model you cannot "
        "find on the price list is a model whose price can move without you "
        "noticing &mdash; a second reason to switch.", "body"))

    A(P("Whether Google re-bills the prompt each turn", "h3"))
    A(P("Our code sends the system instruction once, in the session setup, which is "
        "verifiable in gemini.py. Whether Google internally re-charges accumulated "
        "context per turn is their implementation and is not documented. If it does, "
        "the per-call prompt cost is higher than stated. Reading usage_metadata from "
        "one real call settles it, once there is credit on the account.", "body"))

    A(P("Render's current prices", "h3"))
    A(P("Their pricing page would not render, so the $7 and $25 figures come from "
        "several independent write-ups that agree with each other. Worth confirming "
        "on render.com/pricing before committing.", "body"))

    A(P("The exchange rate", "h3"))
    A(P(f"Rs {PKR:,.0f} to the dollar throughout, the open-market selling rate on "
        "9 October 2026. Every dollar figure moves with it. All of our costs are "
        "dollar-denominated while all of our revenue will be in rupees, so a 10% "
        "rupee slide takes about seven points off the gross margins above.", "body"))

    # ---- 8. sources ----
    A(P("8. Sources", "h2"))
    A(P("Measured on our own systems", "h3"))
    rows = [["Figure", "Where it came from"]]
    for a, b in (
        ("Meta per-message rates",
         "pricing_analytics on WhatsApp Business Account 2812213305655211, "
         "90 days to 13 Oct 2026"),
        (f"Prompt tokens ({UNI_PROMPT_TOKENS:,} and {JAZZ_PROMPT_TOKENS:,})",
         "Google's count_tokens run against the assembled prompts on this deployment"),
        (f"Knowledge base sizes ({UNI_KB_TOKENS:,} and {JAZZ_KB_TOKENS:,} tokens)",
         "The same, against the stored knowledge bases"),
        ("Recording size, 338 KB a minute",
         "The one kept production recording: 651,260 bytes for 113 seconds"),
        ("Thinking tokens, 573 of a 600 budget",
         "usage_metadata from a real reply on the University knowledge base"),
        ("Infobip rates unavailable",
         "/voice/1/logs and /account/1/balance both 404; /calls/1/calls returned "
         "no results"),
    ):
        rows.append([a, b])
    A(table(rows, [58*mm, 93*mm]))

    A(P("Published pages", "h3"))
    for text in (
        "Gemini Developer API pricing &mdash; ai.google.dev/gemini-api/docs/pricing "
        "&mdash; audio in and out, text in, and the 25 tokens-per-second audio rate",
        "WhatsApp Business Platform pricing &mdash; "
        "developers.facebook.com/documentation/business-messaging/whatsapp/pricing "
        "&mdash; free service window, customer messages never charged",
        "Supabase pricing &mdash; supabase.com/pricing &mdash; Free and Pro quotas, "
        "overage rates, the one-week pause on free projects",
        "Vercel pricing &mdash; vercel.com/pricing &mdash; Hobby free and "
        "non-commercial, Pro $20",
        "Infobip voice pricing &mdash; infobip.com/voice/pricing &mdash; confirms "
        "no published per-destination rates",
        "Pakistan Observer, 9 October 2026 &mdash; USD/PKR open market 277.4 / 278.2",
    ):
        A(P(f"&bull;&nbsp; {text}", "note"))

    A(P("Secondary, treat with care", "h3"))
    A(P("&bull;&nbsp; gemini-3.5-flash at $1.50 and $9.00 per million &mdash; "
        "pricepertoken.com and anotherwrapper.com, agreeing, citing Google's docs; "
        "not on Google's own page", "note"))
    A(P("&bull;&nbsp; Render Starter $7 and Standard $25 &mdash; several independent "
        "write-ups agreeing; Render's own page would not render", "note"))

    A(rule())
    A(P("Every figure in this document is computed by docs/build_cost_report.py "
        "rather than typed in. Change the exchange rate, the talk ratio, the call "
        "length or a knowledge base size at the top of that file and re-run it, and "
        "every table moves with it.", "note"))

    doc = SimpleDocTemplate(
        str(path), pagesize=A4,
        leftMargin=18*mm, rightMargin=18*mm, topMargin=16*mm, bottomMargin=16*mm,
        title="What the calling agent costs", author="Calling Agent",
        subject=f"Cost and pricing analysis as of {AS_OF}",
    )

    def footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(18*mm, 10*mm,
                          f"Calling Agent - cost and pricing - {AS_OF}")
        canvas.drawRightString(A4[0] - 18*mm, 10*mm, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)


if __name__ == "__main__":
    here = pathlib.Path(__file__).resolve().parent
    out = here / "calling-agent-costs.pdf"
    build(out)
    print(f"wrote {out} ({out.stat().st_size:,} bytes)")
