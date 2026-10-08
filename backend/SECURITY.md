# Locking down the database

## The problem

Seventeen tables in the Supabase project answer a `select *` from the **anon**
key. That key is not a secret — it is published in client bundles by design,
and anyone who has ever loaded the app already has it.

Verified on 9 October 2026 by calling PostgREST with the published anon key:

| Table | What it returns |
|---|---|
| `calls` | call transcripts |
| `whatsapp_calls` | transcripts and phone number ids |
| `organizations` | contact email addresses |
| `whatsapp_accounts` | phone number ids, display numbers |
| `whatsapp_calling_configs` | phone numbers and configuration |
| `presence_state` | live transcripts |
| plus 11 more | currently empty, same policy |

They were created with `prototype_temp_anon_access`:

```sql
create policy prototype_temp_anon_access on public.<table>
  for all to anon, authenticated using (true) with check (true);
```

Every row, to every role, **read and write**. A reasonable prototype shortcut;
not a reasonable thing to sell on top of.

`voice_tenants`, `voice_calls`, `voice_knowledge` and `voice_messages` were
closed by earlier migrations and are not affected.

## Why closing it breaks nothing

Every caller is server-side, holding the service key, and `service_role`
bypasses RLS entirely:

- the Python API, through `app/db/supabase.py`
- Conversation-Agent, through `getSupabase()` — whose own docstring reads
  *"Server-side Supabase client"* and which prefers `SUPABASE_SERVICE_ROLE_KEY`

The only browser file in that project mentioning Supabase is
`src/lib/firebase.ts`, an unused stub whose comment points at the server
module. No page reads PostgREST directly.

## Running it

1. Supabase dashboard → the **Conversation Agent** project → **SQL Editor** →
   New query.
2. Paste the whole of
   `supabase/migrations/20261009000000_lock_down_remaining_tables.sql`.
3. **Run.** The notices pane lists each table as it is locked, and names any it
   skipped because that table does not exist in this project.

Safe to run twice.

## Checking it worked

From the project's **Settings → API**, copy the `anon` `public` key, then:

```bash
ANON='<your anon key>'
URL='https://forknkxmtpclldcthanx.supabase.co'

for t in calls organizations whatsapp_calls whatsapp_accounts social_messages; do
  printf '%-26s ' "$t"
  curl -s -o /dev/null -w '%{http_code}\n' \
    "$URL/rest/v1/$t?select=id&limit=1" \
    -H "apikey: $ANON" -H "Authorization: Bearer $ANON"
done
```

**You want `401` on every line.** A `200` means that table is still open.

Then confirm the services still work, which proves the service key is
unaffected:

```bash
curl -s https://calling-agent-juk1.onrender.com/health
```

`"database": true` and a working `/api/calls` means nothing broke.

## What the migration also does

```sql
alter default privileges in schema public
  revoke all on tables from anon, authenticated;
```

Without that line, the next table any service creates is public again and
nobody notices until somebody checks. This makes closed the default rather
than something to remember.
