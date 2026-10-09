-- Outbound campaigns: a list of people to call, and how far through it we are.
--
-- Same (id, tenant_id, data jsonb) shape as the sibling tables, so a field can
-- be added to a campaign without another migration.
--
-- Locked to service_role from the start. A campaign row is a list of customers'
-- phone numbers, which is exactly the kind of thing the earlier permissive
-- policy left readable with a published key.

create table if not exists public.voice_campaigns (
  id text primary key,
  tenant_id text,
  data jsonb not null,
  updated_at timestamptz not null default now()
);

-- Every read is "this company, newest first".
create index if not exists voice_campaigns_tenant_idx
  on public.voice_campaigns (tenant_id, updated_at desc);

do $$
begin
  execute 'alter table public.voice_campaigns enable row level security';
  -- No policy for anon or authenticated on purpose: with RLS on and nothing
  -- matching, those roles see nothing and can write nothing.
  execute 'drop policy if exists service_role_only on public.voice_campaigns';
  execute
    'create policy service_role_only on public.voice_campaigns '
    'for all to service_role using (true) with check (true)';
end $$;

revoke all on public.voice_campaigns from anon, authenticated;
