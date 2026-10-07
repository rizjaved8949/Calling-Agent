# Calling Agent — frontend

A browser-only React, TypeScript, Vite and Tailwind frontend for a multi-tenant voice
agent platform. Companies get an AI agent that answers and places calls on a phone
number and on WhatsApp, grounded in their own uploaded material. The public site has
Home, Product, Solutions, Pricing, About, Resources and Contact pages.

## Run

1. `npm install`
2. `npm run dev`
3. Open http://localhost:5173/
4. `npm run build` for a production bundle.

## Three portals, three demo logins

The sign-in screen has a button for each. Any email with a password of six or more
characters also works.

| Portal | Who | Lands on |
| --- | --- | --- |
| **Company admin** | the customer's manager | `/app/dashboard` |
| **Company staff** | whoever takes calls | `/app/queue` |
| **Platform (us)** | the platform operator | `/platform/companies` |

A company account cannot reach `/platform/*` at all. It gets a plain not-found rather
than a permission error, so the portal is never advertised. Staff accounts are pushed
back to their own queue if they try an admin route.

## No technical detail reaches a company

This is enforced, not just styled. A company user never sees a model name, threshold,
chunk size, provider name, webhook URL, credential or raw provider error.

- `src/lib/presets.ts` holds named choices — **Answer strictness**, **Conversation
  pace**, **Voice quality** — each with a company-facing label, description and
  consequence, plus the technical `values` behind it. Only the platform portal reads
  or edits those values.
- Provider failures are translated by `friendlyError()` into a sentence and an action,
  with a short reference code. The raw text stays on the record and appears only in
  **Platform → Health**, beside the same code.
- `BANNED_TERMS` lists the vocabulary that must never appear on a company screen.
  `pages.tsx`, `pages-company.tsx`, `pages-extra.tsx` and `pages-onboarding.tsx` are
  clean of it; the only matches left are code identifiers such as
  `kb.similarityThreshold`, never rendered text.

Credentials are entered by the platform operator on the company's behalf, in
**Platform → company detail**. The input is write-only: the value is discarded on
submit and only the last four characters plus audit metadata are kept.

## Many knowledge bases, chosen per call

A company has several knowledge bases, each for a subject. A call resolves which one to
use in this order, first match wins:

1. an explicit choice made for that call,
2. a routing rule,
3. the agent's default,
4. the company default.

The resolved base and the reason are recorded on the call and shown on call detail, so
"why did it answer that way" is always answerable.

- **Routing** (`/app/routing`) — rules read top to bottom, reorderable, matching on the
  number dialled, the channel, a number prefix, a contact list or business hours. A
  permanent last row catches everything else. Each rule shows its 30-day match count,
  and **Try a call** shows which rule would win before a real caller finds out.
- **Campaigns** (`/app/campaigns`) — outbound lists with their own agent and knowledge
  bases, calling window, attempts and retry gap.
- **Agent → Try it** overrides the knowledge base for a single call.
- **Unanswered** (`/app/unanswered`) — every question the agent declined, ranked by how
  often it was asked, with one click through to the right knowledge base.

## Structure

- `src/app.tsx` — portals, role guards, both shells, routing, shared primitives
- `src/pages.tsx` — sign-in, agents list, live, history, messages, guides, team, usage
- `src/pages-company.tsx` — dashboard, knowledge, agent detail, numbers, settings
- `src/pages-onboarding.tsx` — setup wizard and call detail
- `src/pages-extra.tsx` — knowledge detail, routing, campaigns, unanswered, staff screens
- `src/pages-platform.tsx` — the platform portal
- `src/lib/presets.ts` — the plain-language layer and the banned vocabulary
- `src/lib/types.ts`, `src/lib/fixtures/`, `src/lib/api/` — entities, seed data, mock adapter
- `src/lib/exportXlsx.ts` — client-side spreadsheet export
- `src/marketing.tsx`, `src/marketing.css` — the public site
- `src/style.css`, `src/workspace.css` — tokens, controls, light and dark themes
- `public/*.png` — original site imagery

## Demo behaviour

Frontend only. There is no backend, database, auth provider, telephony or cloud service
of any kind.

- All data goes through `src/lib/api/index.ts` and its in-memory store. `USE_MOCK` is
  the only shipped mode; the real branch throws "not implemented".
- Roughly 400 seeded calls span 90 days across both channels, spread over four
  knowledge bases, with every recording state represented.
- Sessions and theme survive a refresh. Product data resets to the fixtures.
- Uploads, indexing, calls, messages, connection tests, provisioning, live events and
  the contact form are simulations. `READY` recordings play a generated sample asset.
- A read-only support session (**View as** in the platform portal) blocks every
  mutation and says so in a banner.
- CSV and XLSX exports are generated in the browser.

The app remains local and is not deployed.
