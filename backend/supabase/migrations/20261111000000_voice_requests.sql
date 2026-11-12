-- Requests callers made on calls, recorded by the agent for the team.
--
-- One row per request: a test drive, a service booking, a complaint, a
-- callback, an unanswered question, a cancellation. The reference the caller
-- was given (CMP-20261111-01 and the like) is inside `data`, with its parts
-- indexed so the day's counter is one query.

create table if not exists public.voice_requests (
  id text primary key,
  tenant_id text not null,
  data jsonb not null,
  updated_at timestamptz not null default now()
);

create index if not exists voice_requests_tenant_idx
  on public.voice_requests (tenant_id, updated_at desc);
create index if not exists voice_requests_day_idx
  on public.voice_requests (tenant_id, (data->>'referenceDay'), (data->>'prefix'));
create index if not exists voice_requests_status_idx
  on public.voice_requests (tenant_id, (data->>'status'));

alter table public.voice_requests enable row level security;
drop policy if exists service_role_only on public.voice_requests;
create policy service_role_only on public.voice_requests
  for all to service_role using (true) with check (true);
revoke all on public.voice_requests from anon, authenticated;
