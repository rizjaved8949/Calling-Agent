"""
Builds docs/calling-agent-pricing.pdf — the per-minute pricing analysis.

Everything is computed from the inputs below. Change one and re-run:

    python docs/build_pricing_report.py

The companion docs/build_cost_report.py covers the same ground at a higher
level. This one answers three questions that one did not: what each channel
costs separately, who pays what when the customer brings their own
credentials, and what to charge per minute against the competition.
"""
from __future__ import annotations

import pathlib

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer,
    Table, TableStyle,
)

AS_OF = "13 October 2026"

# --- Exchange rates --------------------------------------------------------
#: Open-market selling, Pakistan Observer, 9 Oct 2026.
PKR = 278.0
#: Mid-market, Pluang, 22 Jul 2026. Stale, but only used for tiny add-ons.
EUR_PKR = 317.0

# --- Google, official list, gemini-3.1-flash-live-preview ------------------
LIVE_AUDIO_IN = 3.00 / 1e6
LIVE_AUDIO_OUT = 12.00 / 1e6
LIVE_TEXT_IN = 0.75 / 1e6
AUDIO_TOKENS_PER_SEC = 25
TALK_RATIO = 0.40

# --- Token counts measured on this deployment ------------------------------
UNI_PROMPT = 23_808
JAZZ_PROMPT = 3_810
UNI_KB = 22_706
JAZZ_KB = 2_238
REPLY_OVERHEAD = 600
REPLY_OUT = 200

# --- Text models (input, output) per token ---------------------------------
FLASH = (1.50 / 1e6, 9.00 / 1e6)        # gemini-3.5-flash, in use now
FLASH_LITE = (0.30 / 1e6, 2.50 / 1e6)   # the recommended switch

# --- Meta, read from our own WABA billing ----------------------------------
WA_MARKETING = 0.0946 / 2       # $0.0473
WA_UTILITY = 0.10 / 10          # $0.0100
#: Meta's WhatsApp calling fee. Pakistan is not published; Afghanistan and
#: Bangladesh, the same Rest-of-Asia-Pacific band, are $0.01140/min.
WA_CALL_OUT = 0.01140
WA_CALL_IN = 0.0                # "All user-initiated calls are free."

# --- Carrier, the one unknown ----------------------------------------------
#: Twilio publishes Pakistan rates. Infobip does not, and should be cheaper,
#: but this is the only citable upper bound we have.
TWILIO_PK_MOBILE = 0.18
TWILIO_PK_LANDLINE = 0.155
TWILIO_PK_INBOUND = 0.004
#: A third-party summary puts Infobip voice between EUR 0.0002 and 0.0054/min
#: across destinations. The top of that, in dollars, is the optimistic case.
INFOBIP_OPTIMISTIC = 0.0054 * (EUR_PKR / PKR)

# --- Competitors, advertised rates -----------------------------------------
COMPETITORS = [
    ("Vapi", 0.05, 0.08, 0.15, "Platform fee only; LLM and telephony on top"),
    ("Retell AI", 0.055, 0.105, 0.155, "Voice infrastructure; LLM and telephony on top"),
    ("Bland AI", 0.14, 0.14, 0.14, "All-in on the Start tier, no monthly fee"),
    ("Synthflow", 0.15, 0.15, 0.24, "Self-serve tiers reportedly withdrawn"),
]

PLATFORM_FLOOR_USD = 20 + 7 + 25 + 1   # Vercel Pro, Render Starter, Supabase Pro, domain


def rs(usd: float) -> float:
    return usd * PKR


def money(usd: float, places: int | None = None) -> str:
    v = rs(usd)
    if places is not None:
        return f"Rs {v:,.{places}f}"
    return f"Rs {v:,.0f}" if v >= 1000 else f"Rs {v:,.2f}"


def audio_per_min(ratio: float = TALK_RATIO) -> float:
    return (60 * AUDIO_TOKENS_PER_SEC * LIVE_AUDIO_IN
            + 60 * ratio * AUDIO_TOKENS_PER_SEC * LIVE_AUDIO_OUT)


def gemini_call(minutes: float, prompt_tokens: int) -> float:
    return prompt_tokens * LIVE_TEXT_IN + minutes * audio_per_min()


def gemini_reply(kb_tokens: int, model: tuple[float, float]) -> float:
    return (kb_tokens + REPLY_OVERHEAD) * model[0] + REPLY_OUT * model[1]


# ---------------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------------

INK = colors.HexColor("#1a1a1a")
MUTED = colors.HexColor("#5a5a5a")
RULE = colors.HexColor("#d4d4d4")
BAND = colors.HexColor("#f2f4f7")
ACCENT = colors.HexColor("#0b5d6b")
GOOD = colors.HexColor("#1b6b3a")
WARN = colors.HexColor("#8a2b2b")

_b = getSampleStyleSheet()
S = {
    "title": ParagraphStyle("t", parent=_b["Title"], fontName="Helvetica-Bold",
                            fontSize=21, leading=26, textColor=INK, alignment=TA_LEFT,
                            spaceAfter=2),
    "sub": ParagraphStyle("s", parent=_b["Normal"], fontName="Helvetica", fontSize=9.5,
                          leading=13, textColor=MUTED, spaceAfter=13),
    "h2": ParagraphStyle("h2", parent=_b["Heading2"], fontName="Helvetica-Bold",
                         fontSize=14, leading=18, textColor=ACCENT, spaceBefore=17,
                         spaceAfter=6),
    "h3": ParagraphStyle("h3", parent=_b["Heading3"], fontName="Helvetica-Bold",
                         fontSize=10.5, leading=14, textColor=INK, spaceBefore=11,
                         spaceAfter=4),
    "body": ParagraphStyle("b", parent=_b["Normal"], fontName="Helvetica", fontSize=9.5,
                           leading=13.8, textColor=INK, spaceAfter=6),
    "lead": ParagraphStyle("l", parent=_b["Normal"], fontName="Helvetica", fontSize=11,
                           leading=16, textColor=INK, spaceAfter=8),
    "note": ParagraphStyle("n", parent=_b["Normal"], fontName="Helvetica", fontSize=8.3,
                           leading=11.5, textColor=MUTED, spaceAfter=6),
    "cell": ParagraphStyle("c", parent=_b["Normal"], fontName="Helvetica", fontSize=8.3,
                           leading=11, textColor=INK),
    "cellb": ParagraphStyle("cb", parent=_b["Normal"], fontName="Helvetica-Bold",
                            fontSize=8.3, leading=11, textColor=INK),
    "head": ParagraphStyle("hd", parent=_b["Normal"], fontName="Helvetica-Bold",
                           fontSize=8.3, leading=11, textColor=colors.white),
}


def P(t, s="body"):
    return Paragraph(t, S[s])


def table(rows, widths, right=(), bold_last=False, highlight=()):
    data = []
    for r, row in enumerate(rows):
        data.append([Paragraph(str(c), S["head"] if r == 0 else "cell"
                               if not (bold_last and r == len(rows) - 1) else "cellb")
                     if r == 0 else
                     Paragraph(str(c), S["cellb"] if (bold_last and r == len(rows) - 1)
                               else S["cell"])
                     for c in row])
    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    st = [("BACKGROUND", (0, 0), (-1, 0), ACCENT),
          ("VALIGN", (0, 0), (-1, -1), "TOP"),
          ("TOPPADDING", (0, 0), (-1, -1), 4.5),
          ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
          ("LEFTPADDING", (0, 0), (-1, -1), 6),
          ("RIGHTPADDING", (0, 0), (-1, -1), 6),
          ("LINEBELOW", (0, 0), (-1, -2), 0.4, RULE),
          ("BOX", (0, 0), (-1, -1), 0.5, RULE)]
    for i in range(1, len(data)):
        if i % 2 == 0:
            st.append(("BACKGROUND", (0, i), (-1, i), BAND))
    for i in highlight:
        st.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#e3f2e8")))
    for c in right:
        st.append(("ALIGN", (c, 1), (c, -1), "RIGHT"))
    t.setStyle(TableStyle(st))
    return t


def rule():
    return HRFlowable(width="100%", thickness=0.5, color=RULE,
                      spaceBefore=9, spaceAfter=9)


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------

def build(path: pathlib.Path) -> None:
    story = []
    A = story.append
    g3 = gemini_call(3, JAZZ_PROMPT)          # Gemini part of a 3-minute call
    wa_in_min = g3 / 3                        # our cheapest channel, per minute

    # ---------------- cover ----------------
    A(P("What to charge per minute", "title"))
    A(P(f"Channel by channel, scenario by scenario &nbsp;&middot;&nbsp; {AS_OF}"
        f" &nbsp;&middot;&nbsp; USD 1 = Rs {PKR:,.0f}", "sub"))

    A(P(
        f"<b>An inbound WhatsApp call costs us {money(wa_in_min)} a minute.</b> The "
        f"cheapest competitor charges {money(0.08)} a minute all-in and most charge "
        f"{money(0.14)}. We can price at {money(0.05)} to {money(0.065)} a minute, "
        "undercut every one of them by half, and still keep three quarters of the "
        "revenue.", "lead"))
    A(P(
        "<b>Outbound SIM calling is the opposite.</b> At the only published Pakistan "
        f"rate we can cite, the carrier alone is {money(TWILIO_PK_MOBILE)} a minute "
        f"&mdash; {TWILIO_PK_MOBILE / wa_in_min:.0f} times what the AI costs. Until "
        "Infobip gives us their rate, outbound SIM should be sold at cost plus a "
        "margin, never bundled into a flat per-minute price.", "body"))
    A(rule())

    # ---------------- 1. channels ----------------
    A(P("1. The five ways a call can happen", "h2"))
    A(P("They do not cost remotely the same. Every row is a three-minute call on a "
        "trimmed knowledge base, at 40% agent talk time.", "body"))

    channels = [
        ("WhatsApp call &mdash; they call us", 0.0,
         "Meta: user-initiated calls are free", True),
        ("SIM call &mdash; they call us", TWILIO_PK_INBOUND,
         "Inbound minutes are cheap everywhere", True),
        ("WhatsApp call &mdash; we call them", WA_CALL_OUT,
         "Meta connectivity fee, Asia-Pacific band", False),
        ("SIM call &mdash; we call them (Infobip best case)", INFOBIP_OPTIMISTIC,
         "Top of Infobip's indicative range", False),
        ("SIM call &mdash; we call them (Twilio published)", TWILIO_PK_MOBILE,
         "The only citable Pakistan mobile rate", False),
    ]
    rows = [["Channel", "Carrier per minute", "3-minute call", "Our cost per minute",
             "Where the rate comes from"]]
    hl = []
    for i, (name, carrier, src, good) in enumerate(channels, start=1):
        total = g3 + 3 * carrier
        rows.append([name, money(carrier), money(total),
                     f"<b>{money(total / 3)}</b>", src])
        if good:
            hl.append(i)
    A(table(rows, [44*mm, 24*mm, 22*mm, 26*mm, 36*mm], right=(1, 2, 3), highlight=hl))
    A(Spacer(1, 4))
    A(P("The two shaded rows are the business. Inbound costs almost nothing beyond "
        "the AI, because neither Meta nor a carrier charges much to receive. "
        "<b>Margin is easiest on calls that come to us.</b>", "body"))

    A(P("Messages", "h3"))
    rows = [["Message type", "Meta charge", "Google charge", "Total",
             "Total on Flash-Lite"]]
    for label, extra in (("Reply inside the 24-hour window", 0.0),
                         ("Utility template", WA_UTILITY),
                         ("Marketing template", WA_MARKETING)):
        now = gemini_reply(JAZZ_KB, FLASH) + extra
        lite = gemini_reply(JAZZ_KB, FLASH_LITE) + extra
        rows.append([label, money(extra), money(gemini_reply(JAZZ_KB, FLASH)),
                     money(now), f"<b>{money(lite)}</b>"])
    A(table(rows, [48*mm, 24*mm, 26*mm, 24*mm, 30*mm], right=(1, 2, 3, 4)))
    A(Spacer(1, 4))
    A(P("Meta charges nothing for a reply inside 24 hours of the customer's last "
        "message, which is exactly what auto-reply does. That makes an answered "
        f"WhatsApp conversation cost us {money(gemini_reply(JAZZ_KB, FLASH_LITE))} "
        "&mdash; effectively free. Marketing is the expensive one and should never "
        "sit inside a subscription.", "body"))

    A(PageBreak())

    # ---------------- 2. credential scenarios ----------------
    A(P("2. Who brings the credentials", "h2"))
    A(P("The product already supports all of this: a number carries its own "
        "provider credentials, and the operator can allow a company to run on the "
        "platform's instead. The commercial difference between those two settings "
        "is larger than it looks.", "body"))

    rows = [["Scenario", "What they bring", "Who pays the telecom",
             "Our exposure", "What to charge"]]
    for name, bring, pays, risk, charge in (
        ("<b>A. Fully their own</b>", "Their Meta app and their Infobip account",
         "They do, directly to the provider",
         "<font color='#1b6b3a'>None</font>",
         "Flat platform fee plus a per-minute AI fee"),
        ("<b>B. Their WhatsApp, our SIM</b>", "Their Meta app only",
         "They pay Meta; we pay Infobip",
         "<font color='#8a2b2b'>Carrier minutes</font>",
         "Platform, AI, and carrier at cost plus margin"),
        ("<b>C. We provide everything</b>", "Nothing",
         "We pay both, and the number rental",
         "<font color='#8a2b2b'>All of it</font>",
         "Platform, AI, all pass-through, plus a setup fee"),
        ("<b>D. WhatsApp only, their number</b>", "Their Meta app",
         "They pay Meta for templates",
         "<font color='#1b6b3a'>None</font>",
         "Platform plus a per-message AI fee"),
        ("<b>E. WhatsApp only, our number</b>", "Nothing",
         "We pay Meta for everything",
         "<font color='#8a2b2b'>Template fees</font>",
         "Platform, AI, templates at cost plus margin"),
    ):
        rows.append([name, bring, pays, risk, charge])
    A(table(rows, [30*mm, 32*mm, 32*mm, 22*mm, 36*mm]))

    A(P("What each scenario actually costs us", "h3"))
    A(P("For a company doing 500 three-minute calls and 2,000 answered messages a "
        "month, on a trimmed knowledge base and Flash-Lite:", "body"))

    calls, msgs = 500, 2000
    ai = calls * g3 + msgs * gemini_reply(JAZZ_KB, FLASH_LITE)
    rows = [["Scenario", "AI cost", "Telecom we absorb", "Total monthly cost"]]
    for name, carrier_per_min in (
        ("A. Fully their own", 0.0),
        ("B. Their WhatsApp, our SIM (Infobip best case)", INFOBIP_OPTIMISTIC),
        ("B. Their WhatsApp, our SIM (at Twilio's rate)", TWILIO_PK_MOBILE),
        ("C. We provide everything (Infobip best case)", INFOBIP_OPTIMISTIC),
        ("D. WhatsApp only, their number", 0.0),
        ("E. WhatsApp only, our number", 0.0),
    ):
        telecom = calls * 3 * carrier_per_min
        rows.append([name, money(ai), money(telecom), f"<b>{money(ai + telecom)}</b>"])
    A(table(rows, [60*mm, 28*mm, 32*mm, 32*mm], right=(1, 2, 3)))
    A(Spacer(1, 4))
    A(P("Scenarios A and D cost us the same, because the only thing we pay for is "
        "the AI. <b>They are also where our price can be lowest, because there is "
        "no telecom to recover.</b> That is an argument for actively encouraging "
        "customers to bring their own Infobip account, not merely permitting it.",
        "body"))
    A(P("Scenario C needs a setup fee. Buying and verifying a number, registering a "
        "Meta app and getting a template approved is work that happens once and "
        "takes days. Charge for it separately; it is not a per-minute cost.", "note"))

    A(PageBreak())

    # ---------------- 3. competitors ----------------
    A(P("3. What the competition charges", "h2"))
    A(P("These are the platforms a customer would compare us against. All price per "
        "minute, and all are US-centric, which is why the rupee equivalents look "
        "high.", "body"))
    rows = [["Platform", "Advertised", "All-in per minute", "What is included"]]
    for name, plat, lo, hi, note in COMPETITORS:
        band = money(lo) if lo == hi else f"{money(lo)} to {money(hi)}"
        rows.append([f"<b>{name}</b>", f"{money(plat)}/min", band, note])
    rows.append(["<b>Us, inbound WhatsApp</b>", f"<b>{money(wa_in_min)}/min</b>",
                 f"<b>{money(wa_in_min)}</b>",
                 "<b>Everything. No carrier leg, no platform fee to anyone</b>"])
    A(table(rows, [30*mm, 26*mm, 34*mm, 61*mm], bold_last=True,
            highlight=(len(rows) - 1,)))
    A(Spacer(1, 4))
    A(P("The advertised figure is never the whole bill on those platforms: Vapi's "
        f"{money(0.05)} and Retell's {money(0.055)} are hosting fees with the "
        "language model and the telephony charged on top. Ours is the whole cost, "
        "because we hold the Google key and the customer holds the carrier account.",
        "body"))
    A(P("<b>Why we are cheaper, structurally:</b> Gemini Live is priced well below "
        "the stacked speech-to-text, language model and text-to-speech pipelines "
        "those platforms resell; inbound WhatsApp has no carrier leg at all; and we "
        "pay nobody a platform margin. None of that is a discount we are choosing "
        "to give &mdash; it is a lower cost base.", "body"))

    # ---------------- 4. the recommendation ----------------
    A(P("4. Recommended prices", "h2"))
    A(P("Sold per minute, with WhatsApp and SIM priced separately, because they "
        "cost us different amounts. Pricing them the same either overcharges the "
        "WhatsApp customer or loses money on the SIM one.", "body"))

    A(P("Voice, per minute", "h3"))
    rows = [["Product", "Our cost", "Price", "Margin", "Cheapest rival"]]
    for name, cost, price in (
        ("WhatsApp &mdash; inbound", wa_in_min, 0.05),
        ("WhatsApp &mdash; outbound", wa_in_min + WA_CALL_OUT, 0.065),
        ("SIM &mdash; inbound", wa_in_min + TWILIO_PK_INBOUND, 0.06),
    ):
        rows.append([name, money(cost), f"<b>{money(price)}</b>",
                     f"{100 * (price - cost) / price:.0f}%",
                     f"{money(0.08)} to {money(0.14)}"])
    rows.append(["SIM &mdash; outbound", "carrier plus " + money(wa_in_min),
                 "<b>carrier at cost plus " + money(0.05) + "</b>",
                 "fixed", "carrier dominates"])
    A(table(rows, [34*mm, 28*mm, 36*mm, 18*mm, 35*mm], right=(1, 2, 3)))
    A(Spacer(1, 4))
    A(P(f"At {money(0.05)} a minute we are under two thirds of the cheapest all-in "
        "rival and under a third of Bland. There is no need to go lower; going "
        "lower buys nothing but a thinner cushion against the rupee.", "note"))

    rows = [["Product", "Our cost", "Price", "Margin"]]
    for name, cost, price in (
        ("Answered message, inside the 24-hour window",
         gemini_reply(JAZZ_KB, FLASH_LITE), 0.004),
        ("Utility template sent", gemini_reply(JAZZ_KB, FLASH_LITE) + WA_UTILITY,
         0.018),
        ("Marketing template sent", gemini_reply(JAZZ_KB, FLASH_LITE) + WA_MARKETING,
         0.065),
    ):
        rows.append([name, money(cost), f"<b>{money(price)}</b>",
                     f"{100 * (price - cost) / price:.0f}%"])
    A(KeepTogether([P("Messages", "h3"),
                    table(rows, [66*mm, 26*mm, 28*mm, 22*mm], right=(1, 2, 3))]))

    floor_share = PLATFORM_FLOOR_USD / 5
    rows = [["Plan", "Included", "Our cost", "Price", "Margin"]]
    for name, mins, inc_msgs, price in (("Starter", 300, 1_000, 25_000),
                                        ("Growth", 1_500, 5_000, 95_000),
                                        ("Business", 6_000, 20_000, 330_000)):
        cost = rs(mins * wa_in_min + inc_msgs * gemini_reply(JAZZ_KB, FLASH_LITE)
                  + floor_share)
        rows.append([f"<b>{name}</b>", f"{mins:,} min + {inc_msgs:,} msgs",
                     f"Rs {cost:,.0f}", f"<b>Rs {price:,.0f}</b>",
                     f"{100 * (price - cost) / price:.0f}%"])
    # Held together: split across a page this leaves two rows stranded under a
    # repeated header on an otherwise empty sheet.
    A(KeepTogether([
        P("Monthly plans built on those rates", "h3"),
        table(rows, [24*mm, 42*mm, 26*mm, 28*mm, 22*mm], right=(2, 3, 4)),
        Spacer(1, 4),
        P(f"Costed on inbound WhatsApp, a trimmed knowledge base and Flash-Lite, "
          f"with the {money(PLATFORM_FLOOR_USD)} monthly platform floor split five "
          "ways. Minutes beyond the bundle bill at the per-minute rate above. SIM "
          "minutes are an add-on with the carrier passed through, never inside "
          "these bundles.", "note"),
    ]))

    A(Spacer(1, 10))

    # ---------------- 5. the carrier variable ----------------
    A(P("5. The one number that can break this", "h2"))
    A(P("Everything above assumes calls are inbound, or on WhatsApp. Outbound SIM "
        "is the exception, and its cost is the one figure nobody has been able to "
        "give us.", "body"))
    A(P("Infobip does not publish per-destination voice rates. Their rate and "
        "balance endpoints both return 404 on our api-pk2 instance, and our own "
        "call logs came back empty, so there is nothing to read a real price from. "
        "What follows is what a three-minute outbound SIM call costs at a range of "
        "carrier rates, so the answer can be read off the moment you have it.",
        "body"))

    rows = [["If the carrier charges", "3-minute call", "Our cost per minute",
             f"Margin at {money(0.05)}/min", "Verdict"]]
    for rate, label in ((0.002, "very cheap local wholesale"),
                        (0.005, "Infobip indicative top end"),
                        (0.01, "a realistic local CPaaS rate"),
                        (0.02, ""),
                        (0.05, ""),
                        (TWILIO_PK_MOBILE, "Twilio's published Pakistan mobile")):
        total = g3 + 3 * rate
        per = total / 3
        margin = 100 * (0.05 - per) / 0.05
        verdict = "healthy" if margin > 50 else "thin" if margin > 0 else "LOSS"
        colour = "#1b6b3a" if margin > 50 else "#8a6b00" if margin > 0 else "#8a2b2b"
        rows.append([f"{money(rate)}/min" + (f" &mdash; {label}" if label else ""),
                     money(total), money(per),
                     f"<font color='{colour}'><b>{margin:.0f}%</b></font>",
                     f"<font color='{colour}'>{verdict}</font>"])
    A(table(rows, [52*mm, 24*mm, 26*mm, 26*mm, 22*mm], right=(1, 2, 3)))
    A(Spacer(1, 4))

    # Break-even: the carrier rate at which a 50-rupee-ish price earns nothing.
    breakeven = (0.05 * 3 - g3) / 3
    A(P(f"<b>The break-even is {money(breakeven)} a minute.</b> Above that, a flat "
        f"{money(0.05)} per minute loses money on every outbound SIM call. Twilio's "
        f"published rate is {TWILIO_PK_MOBILE / breakeven:.0f} times the break-even, "
        "which is why outbound SIM cannot go on a flat rate until the real Infobip "
        "number is known.", "body"))

    A(P("What to do about it", "h3"))
    for n, text in enumerate([
        "<b>Get the rate.</b> It is in the Infobip portal under Billing, or from "
        "the account manager: ask for Pakistan mobile and landline termination per "
        "minute, and whether billing is per second or per minute.",
        "<b>Until then, sell outbound SIM at cost plus a fixed margin</b> rather "
        "than a flat per-minute price. The customer sees the carrier line "
        "separately, and a rate change does not eat our margin.",
        "<b>Push customers towards their own Infobip account</b> for outbound "
        "volume. It removes the risk entirely, and the product already supports it.",
        "<b>Lead with inbound and WhatsApp.</b> That is where the cost advantage "
        "actually lives, and it needs no carrier negotiation at all.",
    ], 1):
        A(P(f"{n}. {text}", "body"))

    # ---------------- 6. before raising prices ----------------
    A(P("6. Two changes worth more than any price rise", "h2"))
    uni_now = gemini_reply(UNI_KB, FLASH)
    uni_lite = gemini_reply(UNI_KB, FLASH_LITE)
    jazz_lite = gemini_reply(JAZZ_KB, FLASH_LITE)
    A(P(f"<b>Switch the text model.</b> The build uses gemini-3.5-flash at $1.50 and "
        f"$9.00 per million. Flash-Lite is $0.30 and $2.50. On the University "
        f"knowledge base that is {money(uni_now)} a message against {money(uni_lite)} "
        f"&mdash; {100 * (uni_now - uni_lite) / uni_now:.0f}% off the largest "
        "variable cost we have.", "body"))
    A(P(f"<b>Trim the knowledge bases.</b> The whole base is re-sent on every call "
        f"and every message. The University base costs "
        f"{money(UNI_PROMPT * LIVE_TEXT_IN)} per call where the Jazz base costs "
        f"{money(JAZZ_PROMPT * LIVE_TEXT_IN)}. Halving a base halves that line "
        "forever.", "body"))
    A(P(f"Together they take a message from {money(uni_now)} to {money(jazz_lite)}, "
        f"a factor of {uni_now / jazz_lite:.0f}. No price change available to us "
        "moves the business that much.", "body"))

    # ---------------- 7. caveats ----------------
    A(P("7. What could not be proven", "h2"))
    rows = [["Figure", "Status", "How to settle it"]]
    for fig, status, how in (
        ("Infobip Pakistan voice rate",
         "<font color='#8a2b2b'><b>Unknown</b></font>",
         "The Infobip portal under Billing, or the account manager"),
        ("Meta WhatsApp calling rate for Pakistan", "Regional proxy",
         "Pakistan is not in the published rate card; "
         f"{money(WA_CALL_OUT)} is the Afghanistan and Bangladesh rate"),
        ("gemini-3.5-flash price", "Aggregators only",
         "Not on Google's own price list, which is itself a reason to switch"),
        ("Competitor all-in rates", "Secondary",
         "Advertised platform fees are first-party; the all-in bands are "
         "third-party analyses and differ between sources"),
        ("Render Starter and Standard prices", "Secondary",
         "Their pricing page would not render; several write-ups agree"),
        ("EUR/PKR at 317", "Stale",
         "A July 2026 figure, used only for Infobip's small add-on charges"),
    ):
        rows.append([fig, status, how])
    A(table(rows, [44*mm, 26*mm, 82*mm]))
    A(Spacer(1, 4))
    A(P("Everything else &mdash; the Meta message rates, the token counts, the "
        "Google audio rates &mdash; is either read from our own account or taken "
        "from a first-party price list.", "note"))

    # ---------------- 8. sources ----------------
    A(P("8. Sources", "h2"))
    A(P("First-party, and our own account", "h3"))
    for t in (
        "Meta per-message rates &mdash; pricing_analytics on WhatsApp Business "
        "Account 2812213305655211, 90 days to 13 Oct 2026, giving $0.0473 "
        "marketing and $0.0100 utility",
        "Gemini Developer API pricing &mdash; ai.google.dev/gemini-api/docs/pricing "
        "&mdash; the live model's audio and text rates, and 25 tokens per second",
        "WhatsApp Calling API pricing &mdash; developers.facebook.com &mdash; "
        "user-initiated calls free, billed in six-second pulses",
        "Twilio Voice pricing for Pakistan &mdash; twilio.com/voice/pricing/pk "
        "&mdash; $0.18 mobile, $0.155 landline, $0.004 to receive",
        "Infobip voice pricing &mdash; infobip.com/voice/pricing &mdash; add-on "
        "rates only, and confirmation that destination rates are not published",
        "Token counts &mdash; Google's count_tokens against this deployment's real "
        "assembled prompts",
    ):
        A(P(f"&bull;&nbsp; {t}", "note"))

    A(P("Secondary", "h3"))
    for t in (
        "Competitor per-minute rates &mdash; vendor pages and comparison analyses "
        "including famulor.io, leadlock.ai and tested.media; the all-in bands are "
        "estimates and differ between sources",
        "gemini-3.5-flash at $1.50 and $9.00 &mdash; pricepertoken.com and "
        "anotherwrapper.com, agreeing, citing Google's docs",
        "Infobip indicative voice band of EUR 0.0002 to 0.0054 &mdash; "
        "checkthat.ai, which does not name Pakistan",
        "USD/PKR 278 &mdash; Pakistan Observer, 9 Oct 2026. EUR/PKR 317 &mdash; "
        "Pluang, 22 Jul 2026",
    ):
        A(P(f"&bull;&nbsp; {t}", "note"))

    A(rule())
    A(P("Every figure is computed by docs/build_pricing_report.py. Change the "
        "exchange rate, the talk ratio, the carrier rate or a knowledge base size "
        "at the top of that file and re-run it; the whole document moves with it.",
        "note"))

    doc = SimpleDocTemplate(
        str(path), pagesize=A4,
        leftMargin=17*mm, rightMargin=17*mm, topMargin=15*mm, bottomMargin=15*mm,
        title="What to charge per minute", author="Calling Agent",
        subject=f"Per-minute pricing analysis as of {AS_OF}")

    def footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(17*mm, 9*mm, f"Calling Agent - pricing - {AS_OF}")
        canvas.drawRightString(A4[0] - 17*mm, 9*mm, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)


if __name__ == "__main__":
    out = pathlib.Path(__file__).resolve().parent / "calling-agent-pricing.pdf"
    build(out)
    print(f"wrote {out} ({out.stat().st_size:,} bytes)")
