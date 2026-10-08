# Calling Agent API

The backend for the SaaS: each customer company gets its own calling agent —
SIM calls, WhatsApp calls, WhatsApp messaging — with call recordings saved to
**that company's own Google Drive**.

FastAPI, Python 3.12, Supabase for persistence.

---

## Why it is shaped this way

The three services this is ported from were each **single-tenant**: one
company's Meta token in one `.env`, one Google refresh token owning every
recording, and onboarding a customer meant editing an environment variable and
redeploying.

This one is multi-tenant by construction:

- **No customer credential is in the environment.** Each company's WhatsApp
  access token, app secret, Infobip key and Google refresh token live in their
  own row, sealed with AES-256-GCM before the write (`app/security/secret_box.py`).
  A leaked database key is not enough to impersonate a customer.
- **The tenant is derived from the API key**, never from a path or query
  parameter. There is no request shape that asks for another company's data —
  see `app/api/deps.py`.
- **Each company's recordings go to their own Drive**, under their own quota.
  Revoking our access in their Google settings cuts us off completely.

The credential format is deliberately identical to Conversation-Agent's
`voice/secretBox.ts`, and the tables are the ones its migrations already
define, so the TypeScript service and this API read and write the same rows.

---

## Running it

```bash
cd backend
pip install -r requirements-dev.txt
cp .env.example .env        # then fill it in — see below
python -m app.main          # http://localhost:8000
```

A virtual environment is optional and there is none checked in. If you want one,
`python -m venv .venv` then activate it before the install; `.venv/` is already
ignored.

`GET /health` reports what this process can actually do:

```json
{"status":"ok","capabilities":{"database":true,"credentialEncryption":true,
 "transcoding":true,"objectStorage":true,"googleOAuth":true,"adminApi":true}}
```

Every subsystem is optional and fails quietly when unconfigured, so this
endpoint is the answer to "why isn't it recording" without reading someone's
environment over their shoulder.

Interactive API docs: `http://localhost:8000/docs`.

### The three values you must set

| Variable | Why |
|---|---|
| `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` | Without them the service answers webhooks and forgets them. |
| `CREDENTIALS_SECRET` | Customer tokens are stored in plain text without it. Use the **same value** as the TypeScript service. |
| `ADMIN_API_KEY` | Blank disables company onboarding entirely, rather than leaving it open. |

### Database

Apply `supabase/migrations/` in the Supabase SQL editor. The `voice_tenants`
and `voice_calls` tables come from Conversation-Agent and are **not**
redefined here; this adds `voice_messages` and the indexes the lookups need.

Two of those migrations are not optional before real customers:

- `..._voice_messages_and_indexes.sql` creates the message log and the indexes
  the webhook lookups need. Without it `/api/messages` returns a 502 that says
  so.
- `..._lock_down_voice_calls.sql` closes the `anon` policy the call log was
  created with. A call row holds a phone number, a transcript and a recording
  key, and the anon key is published in client bundles by design. Both services
  reach PostgREST with the service key from the server, so nothing breaks.

**Without Supabase**, rows go to `data/local-store.json` so the stack runs with
nothing installed. It is a development convenience and says so at startup and
in `/health` (`localStore: true`): on a host whose filesystem is replaced on
deploy, every deploy erases it.

---

## Tests

```bash
pytest        # 110 tests
```

They run against an in-memory stand-in for PostgREST, so no network and no
Supabase project is needed. Two groups are worth knowing about:

- **Tenant isolation** (`test_api.py`) — company B cannot read, download or
  delete company A's calls or recordings, and B's app secret does not validate
  a webhook addressed to A.
- **Transcoding** (`test_transcoding.py`) — runs real ffmpeg, and asserts the
  output is actually seekable. Skipped where ffmpeg is absent.

---

## Onboarding a company

```bash
# 1. Register them. The API key is returned ONCE and never again.
curl -X POST localhost:8000/api/companies \
  -H "X-Admin-Key: $ADMIN_API_KEY" -H 'Content-Type: application/json' \
  -d '{"phoneNumberId":"123456","name":"Northwind","wabaId":"789",
       "accessToken":"EAAG…","appSecret":"…","verifyToken":"…",
       "defaultCountryCode":"44"}'

# 2. They connect their Drive — returns a Google consent URL to open.
curl -X POST localhost:8000/api/google/connect -H "Authorization: Bearer $COMPANY_KEY"

# 3. Point Meta's webhook at:
#    <PUBLIC_BASE_URL>/api/webhooks/whatsapp
```

`phoneNumberId` is Meta's own `phone_number_id`. It is the tenant key because
Meta puts it on every webhook and every Graph call, so it is the natural
identifier rather than a surrogate.

---

## API

All company routes take `Authorization: Bearer <company api key>`.
Admin routes take `X-Admin-Key`, and may add `X-Company-Id` to look at one
customer without holding their key.

| | |
|---|---|
| `GET /api/health` | Liveness and capabilities |
| `GET /api/companies/me` | What this key belongs to, with credential and channel state |
| `PATCH /api/companies/me` | **The settings screen**: a company connecting its own accounts |
| `GET /api/companies/me/channels` | Which channels work, and what each still needs |
| `POST /api/companies` · `PATCH` · `DELETE` · `/rotate-key` | Onboarding (admin) |
| `GET /api/calls` · `/stats` · `/{id}` · `/{id}/transcript` | The call log |
| `POST /api/calls` | Place an outbound call |
| `POST /api/calls/{id}/end` · `DELETE /api/calls/{id}` | Finish, remove |
| `POST /api/calls/{id}/recording` | Upload audio the browser recorded |
| `GET /api/calls/{id}/recording` | Play it. `?download=true`, `?link=true`, `?token=` |
| `POST /api/calls/{id}/recording/link` | A short-lived URL an `<audio>` element can load |
| `POST /api/calls/{id}/recording/fetch` | Pull the carrier's copy and keep it |
| `POST /api/calls/{id}/recording/problem` | Why the browser could not record |
| `GET/POST /api/messages` · `/text` · `/template` · `/templates` | WhatsApp messaging |
| `GET /api/google/status` · `/connect` · `/disconnect` · `/sync-report` | Drive |
| `GET /api/exports/calls.xlsx` | The master workbook |
| `POST /api/webhooks/whatsapp` · `/infobip/{id}` | Inbound events |

---

## Deploying to Render

`render.yaml` is the reference. It sits in this folder rather than at the
repository root, which means Render's Blueprint will not pick it up
automatically — create the service from the dashboard with **Root Directory**
set to `backend` and runtime **Docker**, or copy the file to the root if you
want Blueprint deploys.

Docker rather than Render's Python runtime, because the recording path wants
ffmpeg and the Dockerfile installs it. Without it the service still runs; audio
is just stored exactly as it arrived, which means larger files and no seeking.

Three things that are easy to get wrong:

- **The container must listen on `$PORT`.** Render injects it. A container that
  binds 8000 regardless passes its build, starts, and then fails the health
  check with nothing useful in the log. The Dockerfile's `CMD` uses it.
- **`PUBLIC_BASE_URL` must be the real service URL.** The Google OAuth redirect
  is derived from it, Meta posts webhooks to it, and a wrong value fails only
  at the moment a customer tries to connect something.
- **`FRONTEND_URL` must list the Vercel domain**, or every request from the
  browser is refused by CORS with an error that says nothing about the cause.

After the first deploy, point Meta's webhook at
`https://<service>.onrender.com/api/webhooks/whatsapp` and add
`https://<service>.onrender.com/api/google/callback` to the OAuth client's
authorised redirect URIs.

One instance, deliberately: the Google Sheet sync debounces in process memory,
so a second worker would mean two uploads racing for the same file. Scale with
instances, not workers.

---

## Credentials: whose are whose

`.env` holds **the operator's own** Meta and carrier credentials, for the
operator's own number. A customer never falls back to them:

- No carrier key of their own means they cannot place a call, and the API says
  `telephony_not_configured`. Falling back would put their minutes on your bill
  and your caller ID on their calls.
- A webhook is verified against **that company's** app secret only. Verifying
  against yours would let any company able to sign with it forge another's
  traffic.

Customer credentials are written through `PATCH /api/companies/me`, sealed
before storage, and are **write-only**: what comes back is whether a value is
set and its last four characters. No endpoint returns a secret, so a screenshot
or a shared session gives nothing away.

---

## Moving recordings into Drive

`scripts/drive_migration.py` copies recordings that are in Supabase Storage
into a company's Drive and repoints their rows.

```bash
# Attach a refresh token you already have, instead of the consent screen.
python scripts/drive_migration.py connect --tenant <id> --credentials <file>

python scripts/drive_migration.py migrate --tenant <id> --dry-run
python scripts/drive_migration.py migrate --tenant <id>
```

**Nothing is deleted.** After a row moves, the audio exists in both places and
the row remembers both: `recordingPath` points at Drive, and `recordingObject`
still points at the bucket. That is what makes Supabase the backup rather than
a previous location — and it is also what keeps the recording visible in
Conversation-Agent's dashboard, which has never heard of a `gd://` reference.

Each upload is read back and its length checked against the original before the
row is repointed, so an interrupted run leaves rows still playing from Supabase
rather than rows pointing at a half-written file. Re-running skips what is
already in Drive.

`--delete-source` removes the Supabase copy as well. It is not the default and
it asks for confirmation; leave it alone until you trust the copies.

---

## Playing a recording

An `<audio>` element sends no `Authorization` header, so a recording URL behind
bearer auth produces a player with controls that silently do nothing.

`POST /api/calls/{id}/recording/link` mints a token that names one recording,
for one company, for fifteen minutes, and puts it in the URL. The endpoint also
honours `Range`, so seeking works against the server rather than only within
whatever the browser has already buffered.

---

## How a recording is saved

1. Audio arrives — uploaded by the browser, or fetched from the carrier.
2. **Completeness is checked** against the claimed byte count and the known
   call length. A carrier lists a composed recording *before it has finished
   writing it*; a real 4:18 call was once stored as 1:05 that way, and because
   it then looked saved, nothing ever went back for the rest. An incomplete
   file is refused and retried, not stored.
3. **Transcoded to 24 kHz mono MP3.** A seven-minute call drops from 6.3 MB to
   under one, and the file becomes seekable.
4. **Stored**, in this order: the company's Google Drive → Supabase Storage →
   local disk. Drive failing mid-call falls through rather than losing the
   recording.
5. The reference is tagged by backend — `gd://`, `sb://`, or a path — so a row
   written in production is never misread on a developer's machine.

> **A note on the transcode.** The sources ran ffmpeg with output to `pipe:1`
> and `-write_xing 1`, intending seekable files. ffmpeg cannot seek backwards
> on a pipe, so the Xing header was silently never written and players guessed
> the duration from the bitrate — scrubbing landed in the wrong place. This
> build writes to a temporary file, deleted in a `finally`. `test_transcoding.py`
> asserts the header is there.

---

## What is not in this build

**The live media bridge.** Carrying call audio to a realtime model — aiortc
WebRTC termination, Infobip SIP, OpenAI Realtime sessions, the mixing recorder
— is not here. The interfaces it would plug into exist and are marked:

- `services/whatsapp.py` → `place_call()` takes the SDP offer the media layer
  would produce.
- `POST /api/calls` refuses `WHATSAPP_CALL` with `501`, by name, rather than
  failing somewhere less obvious.
- An inbound WhatsApp call is **logged** — who rang, when, how it ended — and
  settles as `ABSENT` for recording, because nothing in this process held the
  audio.

Everything else is live: companies, credentials, the call log, recording save
and retrieval, Google Drive, WhatsApp messaging and templates, the workbook and
its Sheet copy, and both webhooks.

The reference implementations to port from, when that milestone starts:

| Source | What to take |
|---|---|
| `University-Calling-Agent/Backend/app.py` | Infobip SIP + OpenAI Realtime, the `LiveRecorder` mixer, RAG over a PDF |
| `Whatsapp-Calling-and-Messaging-Automation/Backend/app.py` | Meta WhatsApp Calling: WebRTC media termination, two-way recording |
| `Conversation-Agent/voice/` | The TypeScript port of both, and `secretBox.ts` |
