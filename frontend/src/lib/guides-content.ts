/**
 * Real guides for the real product — not fixture placeholder text.
 *
 * Used only in LIVE builds (see pages.tsx's Guides/GuideDetail). The offline
 * demo keeps its own fixture guides, which describe a different, invented
 * workspace and would be actively wrong advice for someone's real account.
 *
 * `bodyMd` renders as one continuous paragraph (GuideDetail does not parse
 * markdown), so each is written as prose rather than headed sections.
 *
 * These describe the current flow: a number carries its own credentials, is
 * verified against the provider, and is then given an agent for incoming calls,
 * outgoing calls, or both. An earlier version of this file described entering
 * credentials once in Settings and routing numbers with "call setups", which is
 * not how any of it works now.
 */
import type { Guide } from './types';

const UPDATED = '2026-10-09';

export const REAL_GUIDES: Guide[] = [
  {
    slug: 'getting-started',
    title: 'Getting started',
    category: 'Setup',
    readingMinutes: 4,
    updatedAt: UPDATED,
    bodyMd:
      'There are four steps, and the Dashboard tracks them for you. First, Numbers → Connect a number: choose whether you bought it in Infobip (a phone line anyone can dial) or got it from Meta (a WhatsApp Business number), say whether it is for incoming calls, outgoing calls or both, and paste the credentials from that provider\'s own console — every field says exactly where to find it. The moment you save, we ask the provider whether those credentials work and tell you yes or no, so a typo is caught here rather than on a customer\'s call. Second, paste the webhook URL the page then shows into Infobip or Meta, which is how calls and messages reach us at all. Third, Agents → New agent: write its opening line and a short description of who it is. Fourth, Knowledge: create a knowledge base, upload your documents, and pick it on the agent. Then go back to Numbers and choose that agent as the number\'s inbound agent, its outbound agent, or both. That last click is what turns the number on: until an inbound agent is chosen, incoming calls are declined rather than answered badly.',
  },
  {
    slug: 'numbers',
    title: 'Connecting a number',
    category: 'Setup',
    readingMinutes: 5,
    updatedAt: UPDATED,
    bodyMd:
      'You bring your own number and your own provider account, so your traffic and your bill are yours. For a phone line, buy a voice-capable number in Infobip, then copy your base URL (the top of the Infobip portal home page, something like xxxxx.api.infobip.com) and an API key with the Voice/Calls scope. For WhatsApp, in Meta for Developers open your app → WhatsApp → API Setup and copy the Phone number ID (not the phone number itself) and the WhatsApp Business Account ID; generate the access token as a System User token under Business Settings → System users, because the temporary token on that page expires in 24 hours and your agent will stop answering when it does. You also need the App secret from App settings → Basic, which is how we check that each webhook genuinely came from Meta rather than from someone pretending to be them. The webhook verify token is any phrase you invent — type the same one into Meta when you add the callback URL. After saving, the number shows Verified, or Not verified with the provider\'s own reason. Changing any credential later sends the number back to be verified again, so "verified" always means these exact values were accepted. Editing a number never shows a saved secret back; leave a secret field empty to keep the stored one.',
  },
  {
    slug: 'routing',
    title: 'Choosing who answers a number',
    category: 'Setup',
    readingMinutes: 3,
    updatedAt: UPDATED,
    bodyMd:
      'On the Numbers page, each verified number has up to two slots: the agent that answers calls coming in, and the agent that makes calls going out. An agent is marked inbound, outbound or both, and only fits a matching slot — so a sales agent written for outbound work cannot be dropped onto your support line by accident. Leave the inbound slot empty and incoming calls are declined, which is deliberate: a number answered by whatever agent happened to exist is worse than a number that does not answer. Leave the outbound slot empty and the number can still be used by a person from the Dialer, but your agent will not call out on it. Changing how a number behaves is changing which agent sits in its slot; nothing else about the number moves, and calls already made keep the record of which agent and which documents answered them. A call also records why that knowledge was chosen, which is what makes "why did it say that?" answerable weeks later.',
  },
  {
    slug: 'knowledge-writing',
    title: 'What to upload, and how to write it',
    category: 'Knowledge',
    readingMinutes: 5,
    updatedAt: UPDATED,
    bodyMd:
      'Your agent answers only from what you upload, and says it will have someone call back when the answer is not there — it never invents a price, a date or a policy. That makes the quality of your documents the quality of your agent. Write short, direct answers to the questions people actually ask on the phone: opening hours, prices, what is included, how long something takes, what to bring, where to park. Prefer plain sentences over tables and bullet lists, because the agent is speaking, not showing a page, and a table read aloud is unbearable. Avoid anything that goes stale without someone noticing — "next Tuesday" rather than a date, a price that changed last month — since a confidently wrong answer costs you more than no answer. One fact per sentence, and spell out abbreviations the first time. PDFs, text, markdown and CSV are all read; a scanned PDF with no text layer has nothing to extract, so export a text-based one.',
  },
  {
    slug: 'multiple-knowledge',
    title: 'Keeping separate sets of documents',
    category: 'Knowledge',
    readingMinutes: 3,
    updatedAt: UPDATED,
    bodyMd:
      'A knowledge base is a named set of documents, and an agent reads exactly one of them. That is what lets a support line answer from the support handbook while an outbound sales agent works from the price list on the same account, without either one quoting the other\'s material. Create a base per audience rather than per file, mark one as the default so anything unassigned still has somewhere to fall back to, and give each a purpose in your own words — you read that line when choosing between them months later. An agent with no base reads everything you have uploaded, which is fine for a company with one pile of documents and wrong as soon as you have two. Deleting a base keeps its documents but leaves them unfiled, so every agent can read them again; a base a live number currently answers from is refused rather than deleted out from under it.',
  },
  {
    slug: 'persona',
    title: 'Writing the agent itself',
    category: 'Agents',
    readingMinutes: 4,
    updatedAt: UPDATED,
    bodyMd:
      'An agent has an opening line, a description of who it is, a language, tone notes and rules for when to hand over to a person — and these apply to both phone and WhatsApp conversations, so you write them once. Write the description as if briefing a new colleague on their first morning: "You are Sara, the support assistant for Acme. You are warm and brief. You never promise a refund." Keep the opening line to one sentence, since it is the first thing a caller hears and a paragraph sounds like a recording. Tone notes are where the hard rules go: what never to promise, what always to confirm, how to say no. The language field is only a starting point — the agent follows a caller who speaks something else. The page holds your changes as a draft and saves when you press Save, so a half-written sentence is never what the next caller hears.',
  },
  {
    slug: 'dialer',
    title: 'Calling someone yourself',
    category: 'Calls',
    readingMinutes: 3,
    updatedAt: UPDATED,
    bodyMd:
      'The Dialer lets a person call a customer from one of the company\'s numbers and talk through their own browser. The customer sees the company number, not the employee\'s phone, and no agent is involved — your microphone is the one on the call. Staff you invite land on this screen, which is the point: they can make and take calls without being able to change settings, credentials or the team. You need a verified number set to outgoing or both; it does not need an outbound agent, because you are the one speaking. The browser asks permission for your microphone the first time, and the call ends when you hang up or leave the page. On WhatsApp there is one extra rule: a business may only call someone who has allowed it, so send the permission request first — they accept once, and after that you can call them.',
  },
  {
    slug: 'campaigns',
    title: 'Working through a list of numbers',
    category: 'Calls',
    readingMinutes: 4,
    updatedAt: UPDATED,
    bodyMd:
      'A campaign is a list of people your agent calls one at a time, with a gap between calls, and nobody is redialled automatically. Choose which of your numbers it calls from and which agent works the list — leave the agent empty and the number\'s own outbound agent is used. Paste the numbers one per line, optionally with a name after a comma. Pacing is deliberately conservative: one call at a time rather than several, because the aggressive dialling patterns are exactly where the regulatory trouble lives. Pause stops after the call in progress rather than cutting someone off mid-sentence. Give the campaign its own opening line when the first sentence should mention why you are calling — "this is Sara from Acme about your enquiry" lands very differently from a generic greeting on a call the person did not ask for. Check your local rules on calling hours and consent before you start; nothing here enforces them for you.',
  },
  {
    slug: 'whatsapp-messages',
    title: 'WhatsApp messages and the 24-hour window',
    category: 'Calls',
    readingMinutes: 3,
    updatedAt: UPDATED,
    bodyMd:
      'The Messages screen shows one conversation at a time per number. The rule that catches everyone is Meta\'s, not ours: you may send a free-text message only within 24 hours of that person\'s last message to you. Outside that window Meta accepts your request and quietly never delivers it, which is why the compose box tells you which side of the window you are on and switches to templates when it has closed. A template is a message Meta has approved in advance; only approved ones appear in the picker, and any {{1}} placeholders in it are filled in order. Your agent can answer messages on its own — turn that on per number, since one number may be staffed by people and another by the agent. Deleting a message here removes it from your own log only: WhatsApp has no way to unsend, so the other person keeps their copy, and the confirmation says so rather than pretending otherwise.',
  },
  {
    slug: 'recording',
    title: 'Recordings: where they are kept and how to delete one',
    category: 'Operations',
    readingMinutes: 4,
    updatedAt: UPDATED,
    bodyMd:
      'Recording is on by default and can be turned off in Settings → Recordings, which affects new calls only. A finished recording is playable from the call in History: press play and the audio streams with a scrub bar, and Download gives you the file. If you connect Google Drive, each recording is also copied into a folder in your own Google account, under your own storage, so you keep them whatever happens here — and disconnecting Drive leaves those copies alone. Deleting is two separate decisions, deliberately: Delete recording erases only the audio and keeps the record of who called and when, which is what you want when you must stop keeping someone\'s voice but still need the log; Delete call removes the whole thing. Both ask first and say exactly what goes. For a phone call recorded on the carrier\'s side, Fetch from the carrier pulls their copy into your own storage — worth doing, because carriers expire recordings and a dashboard that only ever points at theirs eventually plays nothing. Where you operate may require telling callers they are being recorded; if so, put it in the agent\'s opening line.',
  },
  {
    slug: 'unanswered',
    title: 'Questions your agent could not answer',
    category: 'Operations',
    readingMinutes: 3,
    updatedAt: UPDATED,
    bodyMd:
      'Every time a caller asks something your documents do not cover, the agent offers to have someone call back rather than guessing — and the question is listed under Unanswered. This is not an error log. It is the most direct list you will ever get of what to write next, in your customers\' own words, ranked by how often they ask. Work it from the top: add a short answer to the relevant knowledge base, and the next caller gets it. The list is read out of your calls and messages rather than kept separately, so deleting a call takes its questions with it.',
  },
  {
    slug: 'staff',
    title: 'Adding your team',
    category: 'Operations',
    readingMinutes: 2,
    updatedAt: UPDATED,
    bodyMd:
      'Invite colleagues from Team. They join as staff: they can use the Dialer, take over a live call, look up a customer\'s history and ask your documents a question, but they cannot see credentials, change settings or manage the team. There is one owner per company, which keeps "who can remove whom" unambiguous. No email is sent — this deployment has no mail delivery configured — so you are given a link to pass on yourself, however you normally reach them. The invite names the email address it was issued to and only that person can accept it, so a forwarded link does not let a stranger in. Removing someone stops them signing in; it does not change the company\'s API key, so if you need access cut off immediately that is a separate, deliberate step.',
  },
  {
    slug: 'go-live',
    title: 'Before you let real customers call',
    category: 'Setup',
    readingMinutes: 4,
    updatedAt: UPDATED,
    bodyMd:
      'Check these in order. The number shows Verified on the Numbers page. Its webhook URL is pasted into Infobip, or into Meta with the webhook subscribed to both messages and calls. It has an inbound agent if people will ring it, and an outbound agent if your agent will ring them. That agent has a knowledge base with real documents in it, not an empty one. Then test it properly: ring the number from your own phone and listen as a customer would — does the greeting sound right, does it answer a real question, does it cope with being interrupted, does it say something sensible when you ask about something you have not uploaded. Place one call from the Dialer and confirm the customer sees your company number. Check that the recording plays back in History afterwards. Finally look at the Dashboard: it names anything still wrong, such as a verified number nobody is answering. Only then point your advertising at it.',
  },
];
