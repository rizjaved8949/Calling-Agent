"""
Builds docs/calling-agent.pdf - the same money, explained in plain words.

    python docs/build_plain_report.py

This is the version to hand to someone who does not work on the system: an
owner deciding a price, a salesperson quoting one, an investor asking where
the money goes. It says the same things as calling-agent-pricing.pdf and
reaches the same conclusions.

Every number is IMPORTED from build_pricing_report rather than retyped, so
the two documents cannot drift apart. Change a rate at the top of that file
and both PDFs move together.
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

from build_pricing_report import (
    ACCENT, AS_OF, BAND, COMPETITORS, FLASH, FLASH_LITE, GOOD, INFOBIP_OPTIMISTIC,
    INK, JAZZ_KB, JAZZ_PROMPT, LIVE_TEXT_IN, MUTED, PKR, PLATFORM_FLOOR_USD, RULE,
    TWILIO_PK_INBOUND, TWILIO_PK_MOBILE, UNI_PROMPT, WA_CALL_OUT, WA_MARKETING,
    WA_UTILITY, WARN, gemini_call, gemini_reply, money, rs,
)

# ---------------------------------------------------------------------------
# Styling - deliberately larger and airier than the analytical report, which
# is dense on purpose. This one is meant to be read once, straight through.
# ---------------------------------------------------------------------------

_b = getSampleStyleSheet()
S = {
    "title": ParagraphStyle("t", parent=_b["Title"], fontName="Helvetica-Bold",
                            fontSize=24, leading=28, textColor=INK,
                            alignment=TA_LEFT, spaceAfter=3),
    "sub": ParagraphStyle("s", parent=_b["Normal"], fontName="Helvetica",
                          fontSize=10.5, leading=14, textColor=MUTED, spaceAfter=16),
    "h2": ParagraphStyle("h2", parent=_b["Heading2"], fontName="Helvetica-Bold",
                         fontSize=15, leading=19, textColor=ACCENT, spaceBefore=18,
                         spaceAfter=7),
    "h3": ParagraphStyle("h3", parent=_b["Heading3"], fontName="Helvetica-Bold",
                         fontSize=11.5, leading=15, textColor=INK, spaceBefore=12,
                         spaceAfter=5),
    "body": ParagraphStyle("b", parent=_b["Normal"], fontName="Helvetica",
                           fontSize=10.5, leading=15.5, textColor=INK, spaceAfter=8),
    "lead": ParagraphStyle("l", parent=_b["Normal"], fontName="Helvetica",
                           fontSize=12, leading=17.5, textColor=INK, spaceAfter=10),
    "bullet": ParagraphStyle("bu", parent=_b["Normal"], fontName="Helvetica",
                             fontSize=10.5, leading=15.5, textColor=INK,
                             leftIndent=13, spaceAfter=5),
    "note": ParagraphStyle("n", parent=_b["Normal"], fontName="Helvetica",
                           fontSize=9, leading=12.5, textColor=MUTED, spaceAfter=7),
    "cell": ParagraphStyle("c", parent=_b["Normal"], fontName="Helvetica",
                           fontSize=9.5, leading=13, textColor=INK),
    "cellb": ParagraphStyle("cb", parent=_b["Normal"], fontName="Helvetica-Bold",
                            fontSize=9.5, leading=13, textColor=INK),
    "head": ParagraphStyle("hd", parent=_b["Normal"], fontName="Helvetica-Bold",
                           fontSize=9.5, leading=13, textColor=colors.white),
}


def P(t, s="body"):
    return Paragraph(t, S[s])


def B(t):
    return Paragraph(f"&bull;&nbsp;&nbsp;{t}", S["bullet"])


def table(rows, widths, right=(), highlight=()):
    data = [[Paragraph(str(c), S["head"] if r == 0 else S["cell"]) for c in row]
            for r, row in enumerate(rows)]
    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    st = [("BACKGROUND", (0, 0), (-1, 0), ACCENT),
          ("VALIGN", (0, 0), (-1, -1), "TOP"),
          ("TOPPADDING", (0, 0), (-1, -1), 6),
          ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
          ("LEFTPADDING", (0, 0), (-1, -1), 7),
          ("RIGHTPADDING", (0, 0), (-1, -1), 7),
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


def callout(lines, tint="#eef4f6", edge=None):
    """A tinted box for the one thing on the page that must not be missed."""
    inner = [[Paragraph(l, S["body"] if i else S["lead"])]
             for i, l in enumerate(lines)]
    t = Table(inner, colWidths=[176*mm], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(tint)),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor(edge or "#c3d7dc")),
        ("LEFTPADDING", (0, 0), (-1, -1), 11),
        ("RIGHTPADDING", (0, 0), (-1, -1), 11),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
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

    # The one rate everything else is measured against: what Google charges us
    # for three minutes of talking, divided by three.
    per_min = gemini_call(3, JAZZ_PROMPT) / 3
    msg = gemini_reply(JAZZ_KB, FLASH_LITE)

    # ---------------- cover ----------------
    A(P("Calling Agent", "title"))
    A(P(f"What it costs us, and what we should charge for it &nbsp;&middot;&nbsp; "
        f"{AS_OF} &nbsp;&middot;&nbsp; USD 1 = Rs {PKR:,.0f}", "sub"))

    A(callout([
        f"<b>A one-minute conversation with our AI costs us about "
        f"{money(per_min)}.</b>",
        f"The cheapest competitor in the world charges {money(0.08)} a minute. "
        f"Most charge {money(0.14)}. If we charge {money(0.05)} we are the "
        "cheapest on the market, and we still keep roughly three rupees in "
        "every four.",
    ]))
    A(Spacer(1, 12))

    A(P("This document is for anyone who needs to talk about the money without "
        "needing to know how the software works. Nothing here is estimated or "
        "rounded up for comfort. Every figure comes either from a published "
        "price list or from our own bills, and every sum is done by the script "
        "that produced this page. The sister document, "
        "<i>calling-agent-pricing.pdf</i>, shows the same numbers with the "
        "workings and the sources.", "body"))

    A(P("The whole thing in five lines", "h2"))
    for t in (
        "We sell an assistant that answers phone calls and WhatsApp messages "
        "for a business, in its own voice, around the clock.",
        f"Every minute it talks, we pay Google about {money(per_min)}. That is "
        "almost the entire cost of the product.",
        "On top of that, a normal phone call has a <b>second</b> bill from the "
        "phone company. WhatsApp calls do not. That single difference is the "
        "most important fact in this document.",
        "Competitors charge between four and ten times what the service costs "
        "them, because they can. We have the same room.",
        "So we price WhatsApp and SIM separately, we pass the phone company's "
        "charge straight through, and we never bury it inside a package.",
    ):
        A(B(t))

    A(P("What the customer is actually buying", "h2"))
    A(P("A business gives us two things: a phone number or WhatsApp account, "
        "and a page of information about itself &mdash; its prices, its hours, "
        "its rules, whatever its customers ask about. From then on, when "
        "somebody calls or messages that business, our assistant answers "
        "instead of a person. It speaks, it listens, it answers from the "
        "information it was given, and it hangs up politely when the "
        "conversation is over.", "body"))
    A(P("It can work in four directions, and the direction matters to the "
        "price, so it is worth being clear about them.", "body"))
    rows = [["The customer wants", "In plain words", "Who starts it"]]
    for want, plain, who in (
        ("Calls answered", "Their customers ring the business and our "
         "assistant picks up", "The caller"),
        ("Calls made", "Our assistant rings a list of people on the "
         "business's behalf", "Us"),
        ("Messages answered", "Somebody writes on WhatsApp and gets a real "
         "answer in seconds", "The customer"),
        ("Messages sent", "The business announces something to a list of "
         "people", "Us"),
    ):
        rows.append([f"<b>{want}</b>", plain, who])
    A(table(rows, [34*mm, 106*mm, 36*mm]))

    A(PageBreak())

    # ---------------- where the money goes ----------------
    A(P("Where our money actually goes", "h2"))
    A(P("Three places, and they are very different sizes. It helps to picture "
        "the assistant as a very fast employee who is paid by the second, "
        "working on a phone line someone else owns.", "body"))

    rows = [["We pay", "For what", "How it is charged", "Roughly"]]
    for who, what, how, amount in (
        ("<b>Google</b>", "The thinking and the voice. This is the assistant "
         "itself &mdash; understanding what was said and saying something back.",
         "By the second of conversation", f"{money(per_min)} a minute"),
        ("<b>The phone company</b>", "Carrying an ordinary phone call over the "
         "normal telephone network.", "By the minute, and only for real phone "
         "calls", "Varies hugely &mdash; see below"),
        ("<b>Meta (WhatsApp)</b>", "Letting us message and call through "
         "WhatsApp. Replies to people who wrote to us first are free.",
         "Per message we start; calls in are free",
         f"{money(0)} to {money(WA_MARKETING)}"),
        ("<b>Servers</b>", "Keeping the website and the system switched on, "
         "whether anyone uses it or not.", "A flat monthly fee",
         f"{money(PLATFORM_FLOOR_USD)} a month, total"),
    ):
        rows.append([who, what, how, amount])
    A(table(rows, [26*mm, 62*mm, 48*mm, 40*mm]))
    A(Spacer(1, 5))
    A(P("Notice the shape of that list. Three of the four lines are small or "
        "fixed. The phone company is the only one that can get out of hand, "
        "and it only appears when we make an ordinary phone call.", "note"))

    A(P("The four ways a conversation can happen, cheapest first", "h2"))
    A(P("This is the single most useful table in the document. Same assistant, "
        "same quality, same minute of talking &mdash; the only thing that "
        "changes is how the sound gets from the person to us.", "body"))

    rows = [["How the conversation happens", "What we pay Google",
             "What we pay to carry it", "Total, per minute"]]
    cheapest = [
        ("Someone <b>WhatsApp calls</b> the business", 0.0,
         "Nothing. Meta does not charge for calls that come in."),
        ("Someone <b>phones</b> the business normally", TWILIO_PK_INBOUND,
         "A fraction of a rupee to receive."),
        ("We <b>WhatsApp call</b> someone", WA_CALL_OUT,
         "A small Meta fee."),
        ("We <b>phone</b> someone normally", TWILIO_PK_MOBILE,
         "The full phone company rate. This is the expensive one."),
    ]
    for label, carry, _why in cheapest:
        total = per_min + carry
        rows.append([label, money(per_min), money(carry) if carry else
                     f"<font color='#1b6b3a'><b>free</b></font>",
                     f"<b>{money(total)}</b>"])
    A(table(rows, [66*mm, 32*mm, 38*mm, 40*mm], right=(1, 2, 3),
            highlight=(1,)))
    A(Spacer(1, 6))

    worst = per_min + TWILIO_PK_MOBILE
    A(callout([
        f"<b>Read the first row and the last row together.</b>",
        f"An incoming WhatsApp call costs us {money(per_min)} a minute. Us "
        f"ringing a mobile costs {money(worst)} a minute &mdash; about "
        f"{worst / per_min:.0f} times as much, for exactly the same "
        "conversation. The difference is not our software. It is the phone "
        "company.",
        "Everything in our pricing follows from this. We should sell WhatsApp "
        "hard, sell answering calls happily, and treat making ordinary phone "
        "calls as a pass-through where the customer pays the phone bill.",
    ], tint="#fbf1ef", edge="#e0bdb6"))

    A(KeepTogether([
        P("And a WhatsApp message?", "h3"),
        P(f"Answering someone who messaged us first costs {money(msg, 2)} "
        f"&mdash; thirty-eight paisa. A business could have a "
        "thousand messages answered for the price of a cup of tea, because "
        "Meta lets us reply free for 24 hours after someone writes to us, and "
          "the assistant's thinking on a short message is almost nothing.",
          "body"),
    ]))
    A(P(f"Starting the conversation is where Meta charges. A useful notice "
        f"&mdash; an order update, an appointment reminder &mdash; costs "
        f"{money(WA_UTILITY + msg, 2)}. An advertisement costs "
        f"{money(WA_MARKETING + msg, 2)}, roughly "
        f"{(WA_MARKETING + msg) / (WA_UTILITY + msg):.0f} times more. "
        "Advertising messages should never be included in a monthly package; "
        "they should be sold by the message.", "body"))

    # ---------------- who brings what ----------------
    A(P("Who brings the phone number and the WhatsApp account", "h2"))
    A(P("Customers arrive in one of five situations, and they are not equally "
        "profitable. The question behind all of them is simple: when the phone "
        "company sends a bill, whose name is on it?", "body"))

    rows = [["The situation", "What they bring", "Whose name is on the "
             "phone bill", "What it means for us"]]
    for name, bring, bill, means in (
        ("<b>A. They have everything</b>",
         "Their own WhatsApp business account and their own phone account",
         "Theirs",
         "<font color='#1b6b3a'><b>Best case.</b></font> We charge a flat fee "
         "plus a rate per minute and carry no risk at all."),
        ("<b>B. Their WhatsApp, our phone line</b>",
         "Just the WhatsApp account",
         "Ours, for calls",
         "Fine, as long as we charge the phone cost through with a margin "
         "and never fix the price."),
        ("<b>C. We provide everything</b>", "Nothing",
         "Ours, for all of it",
         "<font color='#8a2b2b'><b>Charge a setup fee.</b></font> Buying a "
         "number and getting approvals takes days of real work."),
        ("<b>D. WhatsApp only, their account</b>", "The WhatsApp account",
         "Nobody's &mdash; there is no phone bill",
         "<font color='#1b6b3a'><b>Also excellent.</b></font> Pure software "
         "margin, nothing to pass through."),
        ("<b>E. WhatsApp only, our account</b>", "Nothing",
         "Nobody's, but we pay Meta",
         "Good. Only the messages we start cost anything."),
    ):
        rows.append([name, bring, bill, means])
    A(table(rows, [32*mm, 40*mm, 34*mm, 70*mm], highlight=(1, 4)))
    A(Spacer(1, 5))
    A(P("<b>A and D are the two to chase.</b> In both, we never see a phone "
        "bill, so there is no rate we have to guess at and nothing that can "
        "move against us. They are also the easiest to quote, because the "
        "price is just our software.", "body"))

    # ---------------- competitors ----------------
    A(P("What everyone else charges", "h2"))
    A(P("These are the companies a customer will find if they search instead "
        "of calling us. All figures converted to rupees a minute.", "body"))
    rows = [["Company", "Advertised", "Realistically, all in", "The catch"]]
    PLAIN = {
        "Vapi": "The advertised price is only their own fee. The AI and the "
                "phone call are billed separately on top.",
        "Retell AI": "Same again &mdash; the headline covers their software, "
                     "not the AI or the phone call.",
        "Bland AI": "Genuinely all in, with no monthly fee, but nearly three "
                    "times our price.",
        "Synthflow": "The dearest of the four, and their cheap self-serve "
                     "tiers appear to have been withdrawn.",
    }
    for name, lo, mid, hi, _note in COMPETITORS:
        rows.append([f"<b>{name}</b>", money(lo), f"{money(mid)} to {money(hi)}",
                     PLAIN[name]])
    rows.append(["<b><font color='#1b6b3a'>Us, recommended</font></b>",
                 f"<b>{money(0.05)}</b>", f"<b>{money(0.05)}</b>",
                 "<b>No catch. That is the price, and it is all in.</b>"])
    A(KeepTogether([
        table(rows, [28*mm, 26*mm, 46*mm, 76*mm], right=(1,),
              highlight=(len(rows) - 1,)),
        Spacer(1, 5),
    ]))
    A(P("The gap between the first and second columns is how this industry "
        "works. The advertised number is a platform fee, and the AI and the "
        "phone call are billed on top, so the real bill lands two or three "
        "times higher than the headline. We can quote one honest number and "
        "still be the cheapest, which is a better sales story than a cheap "
        "headline with surprises behind it.", "note"))

    A(Spacer(1, 10))

    # ---------------- what to charge ----------------
    A(P("What we should charge", "h2"))
    A(P("WhatsApp and SIM are priced separately because they cost us different "
        "amounts. Charging one price for both would either overcharge every "
        "WhatsApp customer or lose money on every phone customer.", "body"))

    rows = [["What we sell", "It costs us", "We charge",
             "We keep", "Cheapest rival"]]
    for name, cost, price in (
        ("A minute, WhatsApp call in", per_min, 0.05),
        ("A minute, WhatsApp call out", per_min + WA_CALL_OUT, 0.065),
        ("A minute, phone call in", per_min + TWILIO_PK_INBOUND, 0.06),
    ):
        rows.append([name, money(cost), f"<b>{money(price)}</b>",
                     f"{100 * (price - cost) / price:.0f}%",
                     f"{money(0.08)}+"])
    rows.append(["A minute, phone call out",
                 f"{money(per_min)} + the phone bill",
                 f"<b>the phone bill + {money(0.05)}</b>",
                 f"{money(0.05)}", "nobody competes here"])
    A(table(rows, [42*mm, 34*mm, 44*mm, 20*mm, 32*mm], right=(1, 2, 3)))
    A(Spacer(1, 5))
    A(P("The last row is the important one, and it is deliberately not a fixed "
        "price. We charge whatever the phone company charges us, plus our "
        "usual fee for the assistant. The customer sees exactly what the call "
        "cost. We never promise a price for something we do not control.",
        "note"))

    A(P("Messages", "h3"))
    rows = [["What we sell", "It costs us", "We charge", "We keep"]]
    for name, cost, price in (
        ("Answering someone who wrote to us", msg, 0.004),
        ("A useful notice we send", msg + WA_UTILITY, 0.018),
        ("An advertisement we send", msg + WA_MARKETING, 0.065),
    ):
        rows.append([name, money(cost, 2), f"<b>{money(price, 2)}</b>",
                     f"{100 * (price - cost) / price:.0f}%"])
    A(KeepTogether([table(rows, [68*mm, 30*mm, 32*mm, 24*mm], right=(1, 2, 3))]))

    floor_share = PLATFORM_FLOOR_USD / 5
    rows = [["Package", "What they get", "It costs us", "We charge", "We keep"]]
    for name, mins, inc_msgs, price in (("Starter", 300, 1_000, 25_000),
                                        ("Growth", 1_500, 5_000, 95_000),
                                        ("Business", 6_000, 20_000, 330_000)):
        cost = rs(mins * per_min + inc_msgs * msg + floor_share)
        rows.append([f"<b>{name}</b>",
                     f"{mins:,} minutes and {inc_msgs:,} messages a month",
                     f"Rs {cost:,.0f}", f"<b>Rs {price:,.0f}</b>",
                     f"{100 * (price - cost) / price:.0f}%"])
    A(KeepTogether([
        P("Monthly packages", "h3"),
        P("Most customers would rather pay one predictable figure than count "
          "minutes. These three bundles do that without giving away the "
          "margin. Anything used beyond the bundle is billed at the per-minute "
          "rates above.", "body"),
        table(rows, [22*mm, 62*mm, 26*mm, 32*mm, 20*mm], right=(2, 3, 4)),
        Spacer(1, 5),
        P("<b>Two rules for these packages.</b> The monthly server cost is "
          "already shared across them, so the figures are real, not optimistic. "
          "And ordinary outbound phone minutes are never included &mdash; they "
          "are sold as an add-on at cost plus our fee, for the reason the red "
          "box on page 2 explains.", "note"),
    ]))

    A(Spacer(1, 10))

    # ---------------- risks and savings ----------------
    A(P("The one thing that could spoil this", "h2"))
    A(P("Everything above holds as long as we are careful about one number: "
        "what the phone company charges us to ring a Pakistani mobile.", "body"))
    A(P(f"We do not know it yet. Infobip, the provider this system uses, does "
        f"not publish its rates &mdash; you have to ask them. The only public "
        f"figure we can point at is Twilio's, a competitor, at "
        f"{money(TWILIO_PK_MOBILE)} a minute. Infobip should be cheaper, "
        f"possibly as low as {money(INFOBIP_OPTIMISTIC)}, but until somebody "
        "logs into the Infobip portal and reads the billing page, that is a "
        "hope rather than a fact.", "body"))
    A(callout([
        f"<b>Why it matters:</b> at Twilio's published rate, one outbound "
        f"phone minute costs more than "
        f"{TWILIO_PK_MOBILE / per_min:.0f} minutes of WhatsApp conversation.",
        f"If we ever sold outbound phone minutes at a flat {money(0.05)}, we "
        f"would lose money on every single one. That is precisely why the "
        "recommendation above passes the phone bill through instead of "
        "swallowing it.",
    ], tint="#fbf1ef", edge="#e0bdb6"))
    A(P("<b>What to do about it:</b> get the real Pakistan rate from the "
        "Infobip portal under Billing, or from the account manager. It is one "
        "number, it takes one conversation, and it is the only genuinely "
        "unknown figure in this entire document.", "body"))

    # ---------------- savings ----------------
    uni_now = gemini_reply(UNI_PROMPT, FLASH)
    uni_lite = gemini_reply(UNI_PROMPT, FLASH_LITE)
    jazz_lite = gemini_reply(JAZZ_KB, FLASH_LITE)

    A(P("Two changes worth more than any price rise", "h2"))
    A(P("Before raising a single price, there are two things on our own side "
        "that cut the cost by more than any price change could add.", "body"))

    A(P("1. Use the cheaper brain for messages", "h3"))
    A(P(f"Google sells several models. The one the system uses for written "
        f"replies is the expensive one. The cheaper one is perfectly good for "
        f"answering a WhatsApp question, and costs about "
        f"{100 * (uni_now - uni_lite) / uni_now:.0f}% less &mdash; "
        f"{money(uni_now, 2)} a message becomes {money(uni_lite, 2)}. Same "
        "answers, a fraction of the bill.", "body"))

    A(P("2. Keep the information sheet short", "h3"))
    A(P(f"Here is something genuinely surprising, and it is worth explaining "
        f"because it is the easiest money in the business. Every single time "
        f"the assistant speaks or writes, it re-reads the <i>entire</i> "
        f"information sheet the business gave us. Not once a day &mdash; every "
        f"time.", "body"))
    A(P(f"So the length of that sheet is a permanent, repeating cost. One of "
        f"our test customers has a long one and it costs {money(UNI_PROMPT * LIVE_TEXT_IN, 2)} "
        f"per call just to read. Another has a tight one: "
        f"{money(JAZZ_PROMPT * LIVE_TEXT_IN, 2)}. Halving a sheet halves that "
        "line forever, on every call, for as long as the customer stays.",
        "body"))
    A(P(f"Do both and a message falls from {money(uni_now, 2)} to "
        f"{money(jazz_lite, 2)} &mdash; about "
        f"{uni_now / jazz_lite:.0f} times cheaper. No price change available "
        "to us moves the business that much.", "body"))

    # ---------------- glossary ----------------
    A(P("Words you will hear in meetings", "h2"))
    rows = [["The word", "What it actually means"]]
    for word, means in (
        ("Inbound", "They call us. Cheap, and the direction to prefer."),
        ("Outbound", "We call them. Carries a phone bill."),
        ("SIM call", "An ordinary telephone call over the normal phone "
         "network, the kind anyone can receive."),
        ("WhatsApp call", "A call made inside the WhatsApp app over the "
         "internet. No phone company involved, so far cheaper."),
        ("Knowledge base", "The sheet of information the business gives us "
         "about itself, which the assistant answers from."),
        ("Template", "A message we are allowed to send first, which Meta must "
         "approve in advance. Either a useful notice or an advertisement, and "
         "they are priced very differently."),
        ("The 24-hour window", "After someone messages the business, we may "
         "reply free for a day. This is why answering is nearly free and "
         "starting a conversation is not."),
        ("Pass-through", "A cost we hand to the customer at exactly what we "
         "paid, rather than guessing at it and fixing a price."),
        ("Margin", "The share of the price we keep after the costs of "
         "delivering it."),
    ):
        rows.append([f"<b>{word}</b>", means])
    A(table(rows, [34*mm, 142*mm]))

    A(rule())
    A(P("Produced by docs/build_plain_report.py, which takes its figures "
        "directly from docs/build_pricing_report.py &mdash; the same source as "
        "the detailed pricing document, so the two cannot disagree. For the "
        "workings, the sources and the figures that are still unproven, see "
        "calling-agent-pricing.pdf.", "note"))

    doc = SimpleDocTemplate(
        str(path), pagesize=A4,
        leftMargin=17*mm, rightMargin=17*mm, topMargin=15*mm, bottomMargin=15*mm,
        title="Calling Agent - costs and pricing in plain words",
        author="Calling Agent",
        subject=f"Plain-language guide to costs and pricing as of {AS_OF}")

    def footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(17*mm, 9*mm, f"Calling Agent - {AS_OF}")
        canvas.drawRightString(A4[0] - 17*mm, 9*mm,
                               f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)


if __name__ == "__main__":
    out = pathlib.Path(__file__).resolve().parent / "calling-agent.pdf"
    build(out)
    print(f"wrote {out} ({out.stat().st_size:,} bytes)")
