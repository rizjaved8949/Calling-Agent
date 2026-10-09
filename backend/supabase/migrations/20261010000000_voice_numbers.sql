-- A company's connected numbers, each with its own provider credentials.
--
-- One row per number: a SIM line through Infobip or a WhatsApp number through
-- Meta. Credentials are sealed by the backend before they reach this table,
-- and the WhatsApp phone number id inside `data` is how an inbound Meta
-- webhook finds its company.

create table if not exists public.voice_numbers (
  id text primary key,
  tenant_id text not null,
  data jsonb not null,
  updated_at timestamptz not null default now()
);

create index if not exists voice_numbers_tenant_idx
  on public.voice_numbers (tenant_id, updated_at desc);
create index if not exists voice_numbers_meta_idx
  on public.voice_numbers ((data->>'metaPhoneNumberId'));

alter table public.voice_numbers enable row level security;
drop policy if exists service_role_only on public.voice_numbers;
create policy service_role_only on public.voice_numbers
  for all to service_role using (true) with check (true);
revoke all on public.voice_numbers from anon, authenticated;
