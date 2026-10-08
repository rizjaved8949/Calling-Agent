-- WhatsApp message log, plus the indexes the Python API's lookups depend on.
--
-- Additive only. `voice_tenants` and `voice_calls` already exist from
-- Conversation-Agent's migrations and are not redefined here: the TypeScript
-- service and this API read and write the same rows, and a second definition
-- of either is how the two drift apart.
--
-- Same (id, tenant_id, data jsonb) shape as the other two, so a field can be
-- added to a message without a migration.

create table if not exists public.voice_messages (
  id text primary key,
  tenant_id text,
  data jsonb not null,
  updated_at timestamptz not null default now()
);

-- Every read is scoped by tenant; the conversation view adds the counterparty.
create index if not exists voice_messages_tenant_idx
  on public.voice_messages (tenant_id);
create index if not exists voice_messages_counterparty_idx
  on public.voice_messages (tenant_id, (data ->> 'counterparty'));
-- Delivery receipts arrive keyed by Meta's message id and have to find the row
-- they belong to. Without this, every receipt is a sequential scan.
create index if not exists voice_messages_provider_idx
  on public.voice_messages ((data ->> 'providerMessageId'));
create index if not exists voice_messages_updated_idx
  on public.voice_messages (updated_at desc);

-- Locked to service_role from the start, rather than inheriting the permissive
-- policy its sibling tables were created with.
--
-- A WhatsApp message carries a phone number and what somebody said. Nothing
-- reaches this table from a browser: both the Python API and Conversation-
-- Agent's TypeScript service hold the service key and talk to PostgREST from
-- the server. So there is no reason for anon to see it, and the anon key is
-- published in client bundles by design.
do $$
begin
  execute 'alter table public.voice_messages enable row level security';
  execute 'drop policy if exists prototype_temp_anon_access on public.voice_messages';
  -- No policy for anon or authenticated on purpose: with RLS on and no policy
  -- that matches, those roles see nothing and can write nothing.
  execute 'drop policy if exists service_role_only on public.voice_messages';
  execute
    'create policy service_role_only on public.voice_messages '
    'for all to service_role using (true) with check (true)';
end $$;

-- Belt and braces: PostgREST checks column privileges as well as RLS, so
-- revoking here means a misconfigured policy later cannot quietly re-expose it.
revoke all on public.voice_messages from anon, authenticated;

-- A carrier webhook identifies a call by the provider's id, not ours, and has
-- to find the row within the life of an HTTP request.
create index if not exists voice_calls_provider_call_idx
  on public.voice_calls ((data ->> 'providerCallId'));
-- The call list is "this company, newest first", which is two columns.
create index if not exists voice_calls_tenant_updated_idx
  on public.voice_calls (tenant_id, updated_at desc);
