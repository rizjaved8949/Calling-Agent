-- Live chat between a company and the platform operator.
--
-- Two tables for one conversation. `voice_support_messages` is the history,
-- one row per message, ordered by `updated_at` which deliberately holds the
-- message's own time rather than the write time — so ordering by it orders
-- the conversation, including after a message is edited or ticked as read.
--
-- `voice_support_threads` is a per-company summary: who spoke last and how
-- much each side has not read. It is a cache of what the messages already
-- say, kept because drawing the operator's list of every company must not
-- mean scanning every message those companies ever sent. Nothing is permitted
-- or refused on its strength, so a drifted count shows a wrong badge and
-- costs nothing else; `recount` rebuilds one from the messages.

create table if not exists public.voice_support_messages (
  id text primary key,
  tenant_id text not null,
  data jsonb not null,
  updated_at timestamptz not null default now()
);

-- The one query this table serves: one company's thread, most recent first.
create index if not exists voice_support_messages_thread_idx
  on public.voice_support_messages (tenant_id, updated_at desc);

create table if not exists public.voice_support_threads (
  id text primary key,
  tenant_id text,
  data jsonb not null,
  updated_at timestamptz not null default now()
);

create index if not exists voice_support_threads_recent_idx
  on public.voice_support_threads (updated_at desc);

alter table public.voice_support_messages enable row level security;
drop policy if exists service_role_only on public.voice_support_messages;
create policy service_role_only on public.voice_support_messages
  for all to service_role using (true) with check (true);
revoke all on public.voice_support_messages from anon, authenticated;

alter table public.voice_support_threads enable row level security;
drop policy if exists service_role_only on public.voice_support_threads;
create policy service_role_only on public.voice_support_threads
  for all to service_role using (true) with check (true);
revoke all on public.voice_support_threads from anon, authenticated;
