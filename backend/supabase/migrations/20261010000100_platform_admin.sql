-- The platform's single super admin (one row, id 'superadmin'). Holds only
-- scrypt hashes of the password and recovery code; locked to service_role.
create table if not exists public.voice_platform_admin (
  id text primary key,
  tenant_id text,
  data jsonb not null,
  updated_at timestamptz not null default now()
);
alter table public.voice_platform_admin enable row level security;
drop policy if exists service_role_only on public.voice_platform_admin;
create policy service_role_only on public.voice_platform_admin
  for all to service_role using (true) with check (true);
revoke all on public.voice_platform_admin from anon, authenticated;
