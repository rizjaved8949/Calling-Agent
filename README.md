# Calling Agent

A multi-tenant voice agent platform. Companies get an AI agent that answers and
places calls on a phone number and on WhatsApp, grounded in their own uploaded
material. The public site has Home, Product, Solutions, Pricing, About, Resources
and Contact pages.

## Layout

```
frontend/   the React, TypeScript, Vite and Tailwind app (everything runs here today)
backend/    reserved for the API server — empty for now
```

The frontend is still browser-only: it serves itself from an in-memory mock store
in `frontend/src/lib/api`, so nothing depends on the backend yet.

## Run

From the repository root, which forwards to `frontend/`:

1. `npm --prefix frontend install`
2. `npm run dev`
3. Open http://localhost:5173/
4. `npm run build` for a production bundle.

Running the commands inside `frontend/` directly works the same way.

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

**Credentials are entered by the company itself**, on each number's own page
(**Numbers → a number → Connection setup**). The platform portal has the same panel
for support, but it is the company's own screen that owns the job.

This is the one place where provider names are unavoidable — you cannot ask someone
for a Meta app secret without saying so — so the plain-language rule is relaxed there,
and only there. Everything else about the field is still write-only: the input clears
on save, only the last four characters plus who and when are kept, secret fields have
a show/hide toggle while you type, and a saved value can be replaced or removed but
never read back.

## Many numbers per company

A company holds as many numbers as it needs, of each kind, each with a purpose rather
than just a type — "Main admissions line", "Fee office line", "Alumni WhatsApp". One
per kind is marked **main** and is the fallback.

- **Numbers** (`/app/channels`) groups them by kind, shows which agent answers each,
  the call count, and the setup state. **Add another** raises a request; we provision
  it in the platform portal and the company watches it move Requested, Being set up,
  Testing, Active.
- A number's own page names what happens when it rings — which agents answer it and
  which rules target it — and warns, before removal, what depends on it.
- **Agent, Where it answers** ticks individual numbers, grouped by kind, and says when
  another agent already answers one.
- A routing rule can match **one of your numbers** by name. Campaigns and test calls
  choose which number to dial out from, so the right caller ID shows.
- History filters by number called, and the export names it.

The seeded demo company holds three phone lines, two WhatsApp calling numbers and one
messaging number, with the 400 calls spread across them.

## Many knowledge bases, chosen per call

A company has several knowledge bases, each for a subject. A call resolves which one to
use in this order, first match wins:

1. an explicit choice made for that call,
2. a routing rule,
3. the agent's default,
4. the company default.

The resolved base and the reason are recorded on the call and shown on call detail, so
"why did it answer that way" is always answerable.

- **Routing** (`/app/routing`) — rules read top to bottom, reorderable, matching on one
  of your numbers, the channel, the caller's prefix, a contact list or business hours. A
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
