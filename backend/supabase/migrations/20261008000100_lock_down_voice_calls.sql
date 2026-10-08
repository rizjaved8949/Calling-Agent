-- Close the permissive policy on the call log.
--
-- `voice_calls` and `voice_knowledge` were created with
-- `prototype_temp_anon_access` — "for all to anon, authenticated using (true)"
-- — which is every row, to every role, read and write. `voice_tenants` was
-- later narrowed to service_role; these two were not.
--
-- That matters more than it looks. A call row carries the caller's phone
-- number, how long they spoke, the transcript of what they said and the
-- storage key of the recording. The anon key is not a secret: it is designed
-- to be published in client bundles, and anyone who has ever loaded a page of
-- the app has it. With this policy in place, that key reads every customer's
-- call history across every tenant.
--
-- Nothing needs the policy. Both services reach PostgREST from the server with
-- the service_role key — the Python API through its own client, and
-- Conversation-Agent through `getSupabase()`, which is explicitly
-- server-side. service_role bypasses RLS entirely, so revoking anon's access
-- changes nothing about how either one works.
--
-- Safe to run twice.

do $$
begin
  execute 'alter table public.voice_calls enable row level security';
  execute 'drop policy if exists prototype_temp_anon_access on public.voice_calls';
  -- No policy for anon or authenticated on purpose: with RLS on and no policy
  -- that matches, those roles see nothing and can write nothing.
  execute 'drop policy if exists service_role_only on public.voice_calls';
  execute
    'create policy service_role_only on public.voice_calls '
    'for all to service_role using (true) with check (true)';
end $$;

-- PostgREST checks column privileges as well as RLS, so revoking here means a
-- misconfigured policy later cannot quietly re-expose the table.
revoke all on public.voice_calls from anon, authenticated;

-- The knowledge base rows carry the documents a company uploaded, which are
-- their material and not ours to leave readable.
do $$
begin
  if to_regclass('public.voice_knowledge') is null then
    return;
  end if;
  execute 'alter table public.voice_knowledge enable row level security';
  execute 'drop policy if exists prototype_temp_anon_access on public.voice_knowledge';
  execute 'drop policy if exists service_role_only on public.voice_knowledge';
  execute
    'create policy service_role_only on public.voice_knowledge '
    'for all to service_role using (true) with check (true)';
  execute 'revoke all on public.voice_knowledge from anon, authenticated';
end $$;
