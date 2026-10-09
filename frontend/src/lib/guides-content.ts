/**
 * Real guides for the real product — not fixture placeholder text.
 *
 * Used only in LIVE builds (see pages.tsx's Guides/GuideDetail). The offline
 * demo keeps its own fixture guides, which describe a different, invented
 * workspace and would be actively wrong advice for someone's real account.
 *
 * `bodyMd` renders as one continuous paragraph (GuideDetail does not parse
 * markdown), so each is written as prose rather than headed sections.
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
    bodyMd: 'Start in Settings: connect your WhatsApp Business account and, if you take phone calls too, your Infobip carrier account. Once a number shows as connected under Numbers, build an Agent — give it a greeting, a short description of who it is, and point it at a Knowledge Base of your own documents. Then create a Call Setup to actually route a number to that agent: one inbound setup per number (a line can only be answered one way), and as many outbound setups as you want for different kinds of outgoing calls or campaigns. Until you create a call setup, every number falls back to a generic default and answers from everything you have ever uploaded rather than the specific agent and knowledge you built — so this step is what actually turns the agent on.',
  },
  {
    slug: 'knowledge-writing',
    title: 'Writing knowledge your agent can use',
    category: 'Knowledge',
    readingMinutes: 3,
    updatedAt: UPDATED,
    bodyMd: 'Upload PDF, DOCX, TXT or MD files up to 20 MB each from the Knowledge screen. A PDF has its text extracted on upload rather than read fresh on every call, so a scanned image with no selectable text will not work — if a document uploads but your agent cannot answer from it, that is usually why. Write documents the way you would answer a question yourself: plain sentences with the actual number, date or policy in them, not a cross-reference to another document. Your agent is instructed to answer only from what you have given it and to say it will check rather than guess when something is missing — which is also why an outdated price list does more harm than an empty one.',
  },
  {
    slug: 'persona',
    title: 'Shaping how an agent talks',
    category: 'Agents',
    readingMinutes: 3,
    updatedAt: UPDATED,
    bodyMd: 'Each agent under Agents has its own opening line, a short description of who it is ("You are Ayesha, the admissions assistant for…"), a language, tone notes, and rules for when to hand off to a person. These apply on both a phone call and a WhatsApp conversation — one description, both channels. An agent with nothing filled in falls back to your company\'s own default persona in Settings, which is why a brand-new agent still says something sensible before you have written anything for it. Leave Voice empty to use your company\'s default voice rather than naming one per agent.',
  },
  {
    slug: 'multiple-knowledge',
    title: 'Using more than one knowledge base',
    category: 'Knowledge',
    readingMinutes: 3,
    updatedAt: UPDATED,
    bodyMd: 'Create a separate Knowledge Base when two parts of your business should never answer from each other\'s documents — a sales line that should quote prices and a support line that should not, for instance. Mark one as default and it becomes the fallback for any agent that has not been given a base of its own. An agent you have pointed at a specific base answers only from that base\'s documents; a call setup can override that again and point a particular number at a different base still, which is what lets the same agent answer differently depending on which number took the call.',
  },
  {
    slug: 'routing',
    title: 'Which agent answers which number',
    category: 'Numbers',
    readingMinutes: 3,
    updatedAt: UPDATED,
    bodyMd: 'A Call Setup is the answer to "how does this number behave." Incoming is exclusive: a number can only have one enabled inbound setup, because a call arriving on it has to be answered by exactly one agent — creating a second one is refused rather than left to chance. Outgoing has no such limit; build as many outbound setups as you have reasons to call people, each with its own agent and knowledge. A single ad-hoc outbound call can also name its own agent and knowledge base directly, without a saved setup, for a one-off that does not need to be repeated.',
  },
  {
    slug: 'campaigns',
    title: 'Running an outbound campaign',
    category: 'Calling',
    readingMinutes: 3,
    updatedAt: UPDATED,
    bodyMd: 'A campaign works through a pasted list of numbers one at a time, with a gap between each call rather than calling several people at once. There is no automatic redial — someone who did not answer stays off the list until a person decides to call them again, because that is a decision about how much to pester somebody and not one this product makes for you. A campaign remembers which agent and knowledge base it uses, so a list worked for admissions and a list worked for fee reminders can answer completely differently even from the same number. Pausing a campaign lets whichever call is already in progress finish naturally rather than cutting someone off mid-sentence.',
  },
  {
    slug: 'unanswered',
    title: 'Finding what your agent could not answer',
    category: 'Knowledge',
    readingMinutes: 2,
    updatedAt: UPDATED,
    bodyMd: 'The Unanswered screen looks through your call transcripts for the moments your agent deferred — "I will check and get back to you," in whichever language or phrasing it used — and counts how often the same kind of question comes up. A question asked repeatedly is the clearest signal of what to add to your knowledge base next; one asked once might just be an edge case. This reads your existing transcripts rather than needing anything extra configured.',
  },
  {
    slug: 'staff',
    title: 'Adding your team',
    category: 'Team',
    readingMinutes: 2,
    updatedAt: UPDATED,
    bodyMd: 'From Team, an owner can invite someone as staff. There is no email delivery set up yet, so invite creates a link you share yourself rather than one that gets emailed automatically. Staff can sign in, place and receive calls and messages, and use the live queue, lookup and ask-the-documents tools — but cannot create, edit or delete an agent, a knowledge base or a call setup, and cannot invite or remove anyone else. They can switch which existing call setup is active for a number, which is the one configuration action left open to them, because choosing which already-built preset answers right now is a day-to-day decision, not a design one.',
  },
  {
    slug: 'recording',
    title: 'Call recordings',
    category: 'Calls',
    readingMinutes: 2,
    updatedAt: UPDATED,
    bodyMd: 'Turn recording on in Settings and both sides of a call are captured. Open any finished call\'s detail page and the Recording tab plays it back directly, with a Download button next to the player for a local copy. If you have connected Google Drive, a copy is also saved there automatically, under a folder named for your number, so recordings outlive anything deleted here. A call still in progress has nothing to play yet; one marked "preparing" is usually ready within a few minutes of the call ending.',
  },
  {
    slug: 'numbers',
    title: 'Connecting your numbers',
    category: 'Setup',
    readingMinutes: 2,
    updatedAt: UPDATED,
    bodyMd: 'Your numbers come from the accounts you connect in Settings, not from a request made inside this product — a WhatsApp Business number through Meta, and a phone line through your own Infobip account. Once connected, Numbers shows each one\'s status and which calls it has carried. There is no self-serve way to acquire a brand-new number through this screen yet; that happens on the provider\'s own side, then gets connected here the same way.',
  },
  {
    slug: 'go-live',
    title: 'Before you switch an agent live',
    category: 'Setup',
    readingMinutes: 2,
    updatedAt: UPDATED,
    bodyMd: 'Before a number starts taking real calls: upload the documents it should answer from and check what the agent reads with the preview on the Knowledge screen; write and test the opening line and persona; create the call setup that actually routes the number to that agent, since an agent sitting on the Agents screen with no setup pointed at it never answers anything; and decide whether recording should be on, since that is a disclosure obligation in many places, not just a feature toggle. Placing one real test call or sending one real test message to the number yourself is the fastest way to catch anything the checklist missed.',
  },
];
