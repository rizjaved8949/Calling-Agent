# Calling Agent

A multi-tenant voice agent platform. Companies get an AI agent that answers and
places calls on a phone number and on WhatsApp, grounded in their own uploaded
material. The public site has Home, Product, Solutions, Pricing, About, Resources
and Contact pages.

## Layout

```
frontend/   the React, TypeScript, Vite and Tailwind app
backend/    the Python API: companies, calls, recordings, Google Drive, WhatsApp
app.py      runs both, for development
```

Each half owns its own dependencies, its own scripts and its own deployment,
and neither needs the other installed to build. `app.py` only starts them.

The two are wired together at runtime. Set `VITE_API_URL` in
`frontend/.env.local` and the app talks to the backend for everything the
backend covers; leave it unset and it runs on its in-memory fixtures, as it
always did.

## Running both

```bash
python app.py
```

One process runs both halves, prefixes their output with `[api]` and `[web]`,
and stops both on Ctrl+C. It checks the dependencies first, and waits until the
API actually answers before printing its URL rather than printing one and
leaving you to find out. `--install` installs dependencies, `--api-only` /
`--web-only` run one half, `--api-port` / `--web-port` move the ports.

First time:

```bash
cp backend/.env.example backend/.env         # CREDENTIALS_SECRET, ADMIN_API_KEY
cp frontend/.env.example frontend/.env.local
python app.py --install
```

Running each half yourself works the same way — `python -m app.main` in
`backend/`, `npm run dev` in `frontend/`. `app.py` is for working on both at
once and has no part in deployment: Render builds `backend/Dockerfile`, Vercel
builds `frontend/`, and neither knows it exists.

Without Supabase credentials the backend keeps its rows in a local JSON file,
so the whole stack runs with nothing else installed. `GET /health` says which
store is in use.

## How a company sets itself up

1. **Sign up** at `/signup`. The owner lands on the Dashboard checklist.
2. **Numbers → Connect a number.** Pick *Phone line (Infobip)* or *WhatsApp
   (Meta)*, choose inbound / outbound / both, and paste the credentials from
   the provider console (each field says where to find it). The backend asks
   the provider straight away — Infobip's account balance, Meta's phone number
   record — and marks the number **verified** or **not verified** with the
   provider's own reason. Paste the URL it shows into Infobip (call events) or
   the Meta app (callback URL, subscribed to `messages` and `calls`).
3. **Agents → New agent.** Inbound, outbound or both, with greeting, persona and
   language. **Knowledge** → create a base, upload documents, pick it on the agent.
4. **Back on Numbers**, choose the agent that answers incoming calls and the one
   that makes outgoing calls. Change the agent there to change how the number
   behaves. A number with no inbound agent declines calls.
5. **Outbound:** *Live* (agent calls one person), *Campaigns* (agent works a
   list one by one), or the **Dialer** — an employee calls from the company
   number and talks through the browser; the customer sees the company number.
6. **Team → invite** employees. Staff land on the Dialer.

Every number keeps its own credentials, sealed in `voice_numbers` (apply
`backend/supabase/migrations/20261010000000_voice_numbers.sql`). A customer
never falls back to the credentials in `backend/.env` — except a company you
switch to **"Allow your credentials"** in the platform portal (`/ops-login`),
which can then connect a number with *"Use the platform's own credentials"*.
That is how a demo account calls on your numbers.

See [backend/README.md](backend/README.md) for the API and what is not built yet.

## What still needs doing

Live: **https://calling-agent-amber.vercel.app** → **https://calling-agent-juk1.onrender.com**

### Yours — dashboard changes and decisions

Everything in this list needs an account you own. None of it is code.

- [ ] **Google Cloud Console — add the redirect URI.** Credentials → your OAuth
      client (project `strategic-insights-hub`) → Authorised redirect URIs →
      `https://calling-agent-juk1.onrender.com/api/google/callback`. Keep the
      localhost one too. **Verified failing**: Google answers
      `redirect_uri_mismatch`, so no company can connect their Drive.
- [ ] **Infobip — point call webhooks here.** Calls configuration
      *"Conversaigent Voice Agent"* → notification URL →
      `https://calling-agent-juk1.onrender.com/api/webhooks/infobip/674871172379324`.
      The code to answer a SIM call and attach the agent is written and tested;
      without this the carrier never tells us the call connected.
- [ ] **Render — upgrade to Starter.** Free sleeps after 15 minutes (a call
      arriving at a sleeping instance is a missed call) and gives 0.1 vCPU,
      which cannot carry real-time audio. This one gates the whole voice
      feature working under load.
- [ ] **Supabase — upgrade from Free.** It pauses after about a week idle, and
      a paused project does not error: it goes silent.
- [ ] **Publish the Google consent screen.** Testing mode caps you at 100
      users. Needs a privacy policy URL first.
- [ ] **Write the privacy and data-deletion wording.** Google requires one,
      Meta requires both. The pages get built once the commitments are yours
      to make.
- [ ] **Decide the auth model.** Still outstanding; the company API key lives
      in browser storage today.
- [ ] **Set an agent greeting** for your own company, or the agent mirrors the
      caller instead of introducing itself. Settings → agent.
- [ ] **Decide about `calls` webhooks.** They point here now, and WhatsApp
      calls are not answered until the WebRTC bridge lands, so that number
      rings unanswered meanwhile.
- [ ] Billing and usage limits — nothing counts minutes or messages.

- [ ] **Run `supabase/migrations/20261009000100_knowledge_many_documents.sql`.**
      `voice_knowledge` has a unique index on `tenant_id` — one document per
      company — so uploading a second one fails with a duplicate-key error.
      A company wants a price list *and* an FAQ.

~~Run the RLS lockdown migration~~ — **done**, all 21 tables now refuse the
public anon key.

### Mine — being built

- [x] Multi-tenant core, credentials, isolation, call log, recordings, Drive
- [x] WhatsApp messaging both ways, with auto-reply from uploaded material
- [x] The voice agent, and the call session it runs in
- [x] SIM calls, operator takeover, and the test harness that proves them
- [ ] WhatsApp calls — WebRTC, inbound and outbound
- [ ] Every screen in the app wired to the API
- [ ] Sign-in, once the model is chosen
- [ ] Privacy and data-deletion pages

## Deploying

The two halves deploy separately, each from its own folder.

| | Host | Root directory | Config |
|---|---|---|---|
| API | Render | `backend` | [backend/render.yaml](backend/render.yaml) |
| App | Vercel | `frontend` | [frontend/vercel.json](frontend/vercel.json) |

They have to know about each other, and both directions matter:

- Render needs `FRONTEND_URL` set to the Vercel domain, or the browser's
  requests are refused by CORS.
- Vercel needs `VITE_API_URL` set to the Render URL. It is baked in at build
  time, so changing it needs a redeploy rather than a restart.

[frontend/DEPLOY.md](frontend/DEPLOY.md) covers the Vercel side, including why
the SPA rewrite is there. [backend/README.md](backend/README.md) covers Render.

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
