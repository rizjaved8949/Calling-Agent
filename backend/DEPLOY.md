# Deploying the API to Render

Six steps, in this order. The order matters: each half needs the other's URL,
and Google needs both.

---

## Before you start: what the free plan costs you

A free Render service **spins down after about 15 minutes without traffic** and
takes roughly a minute to wake up.

That is fine for a demo and fine while you are building. It is not fine once a
customer's calls depend on it, because:

- A **WhatsApp call webhook** arriving at a sleeping service waits on that cold
  start. Meta does not wait a minute — the call is missed.
- The **first page load** after an idle period takes a minute, which looks like
  the product is broken.
- In-process state is lost on every spin-down. Nothing important depends on it
  — the Google Sheet sync just re-runs — but it is worth knowing.

Move to the Starter plan before onboarding anyone. Nothing in the configuration
changes except `plan:`.

---

## 1. Push the repository to GitHub

Render deploys from a repo. `backend/.env` and `frontend/.env.local` are
git-ignored, so your credentials stay out of it — check before pushing:

```bash
git status --porcelain | grep -E '\.env($|\.local)' && echo "STOP: a .env is staged"
```

No output means you are clear.

---

## 2. Create the service

Render Dashboard → **New** → **Web Service** → connect the repo, then:

| Field | Value |
|---|---|
| Root Directory | `backend` |
| Runtime | **Docker** |
| Dockerfile Path | `./Dockerfile` |
| Instance Type | Free |
| Health Check Path | `/health` |

Render's Blueprint feature only reads a `render.yaml` at the repository root,
and ours is in `backend/` so the repo keeps exactly two project folders. Create
the service by hand as above; `render.yaml` is the reference for what to fill
in. (Copy it to the root if you would rather use Blueprints.)

---

## 3. Set the environment variables

Under **Environment**, add each of these. Copy the values from your local
`backend/.env` — they are the same.

```
SUPABASE_URL
SUPABASE_SERVICE_KEY
CREDENTIALS_SECRET          <- must match Conversation-Agent exactly
ADMIN_API_KEY
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
GEMINI_API_KEY
WHATSAPP_PHONE_NUMBER_ID
WHATSAPP_BUSINESS_ACCOUNT_ID
WHATSAPP_ACCESS_TOKEN
META_APP_SECRET
WHATSAPP_WEBHOOK_VERIFY_TOKEN
DEFAULT_COUNTRY_CODE
INFOBIP_BASE_URL
INFOBIP_API_KEY
APP_ENV=production
```

Two you do **not** set by hand:

- `PUBLIC_BASE_URL` — set it to the service's own URL once Render has assigned
  one, e.g. `https://calling-agent-api.onrender.com`. The Google OAuth redirect
  is derived from it, so a wrong value fails only at the moment a customer
  tries to connect Drive.
- `FRONTEND_URL` — you do not know the Vercel domain yet. Leave it until step 5.

If `CREDENTIALS_SECRET` does not match the value Conversation-Agent uses,
neither service can read the customer credentials the other sealed. That is the
single most damaging variable to get wrong.

---

## 4. Deploy, then check it honestly

```bash
curl -s https://<your-service>.onrender.com/health
```

Read the `capabilities` block rather than just the `"ok"`:

```json
{"database": true, "localStore": false, "credentialEncryption": true,
 "transcoding": true, "objectStorage": true, "googleOAuth": true,
 "adminApi": true, "publicBaseUrl": true,
 "agentKey": true, "agentEngine": "gemini", "agentMediaBridge": false}
```

What each one tells you if it is wrong:

| Field | `false` means |
|---|---|
| `database` | Supabase variables missing — the service answers and forgets |
| `localStore` **true** | it is writing to a JSON file that every deploy erases |
| `credentialEncryption` | `CREDENTIALS_SECRET` missing — credentials stored in plain text |
| `transcoding` | ffmpeg missing from the image — larger files, no seeking |
| `adminApi` | `ADMIN_API_KEY` missing — you cannot register companies |
| `agentMediaBridge` | always false in this build; the voice engine is not here yet |

---

## 5. Connect the two halves

Deploy the frontend to Vercel first — see [../frontend/DEPLOY.md](../frontend/DEPLOY.md) —
then come back and set, on Render:

```
FRONTEND_URL=https://<your-project>.vercel.app
```

Without it every request from the browser is refused by CORS, with an error
that says nothing about the cause. A browser treats each spelling as a separate
origin, so list the custom domain too if you add one, comma-separated.

---

## 6. Point the outside world at it

**Google** — Cloud Console → Credentials → your OAuth client → Authorised
redirect URIs → add exactly:

```
https://<your-service>.onrender.com/api/google/callback
```

A mismatch here is Google's `redirect_uri_mismatch`, the most common reason
this flow fails. Keep the localhost one for development.

**Meta** — your app → WhatsApp → Configuration → Callback URL:

```
https://<your-service>.onrender.com/api/webhooks/whatsapp
```

Verify token: the value of `WHATSAPP_WEBHOOK_VERIFY_TOKEN`. Meta calls the URL
once to check; the service answers the challenge automatically.

> A number sends webhooks to exactly **one** URL. If this number is still
> pointed at the old Render service, moving it here makes that one go deaf.
> Check which host is live before switching, with the Graph subscriptions call
> in `Whatsapp-Calling-and-Messaging-Automation/ARCHITECTURE.md`.

**Infobip** — webhook URL, if you use SIM calling:

```
https://<your-service>.onrender.com/api/webhooks/infobip/<your phone_number_id>
```

---

## Afterwards

Register a company and confirm the whole path works end to end:

```bash
curl -X POST https://<your-service>.onrender.com/api/companies \
  -H "X-Admin-Key: $ADMIN_API_KEY" -H 'Content-Type: application/json' \
  -d '{"phoneNumberId":"...","name":"Test Co","wabaId":"..."}'
```

The response carries their API key **once**. Paste it into the app's
sign-in gate, and the dashboard should load their company with no calls yet.
