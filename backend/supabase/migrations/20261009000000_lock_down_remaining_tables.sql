-- Close the public read on every remaining table.
--
-- Seventeen tables in this project answer a `select *` from the **anon** key —
-- the key that is published in client bundles by design and that anyone who
-- has ever loaded the app already has. Among what it returns today:
--
--   calls                     call transcripts
--   whatsapp_calls            transcripts and phone number ids
--   organizations             contact email addresses
--   whatsapp_accounts         phone number ids and display numbers
--   whatsapp_calling_configs  phone numbers and configuration
--   presence_state            live transcripts
--
-- They were created with `prototype_temp_anon_access` — "for all to anon,
-- authenticated using (true) with check (true)", which is every row, to every
-- role, read *and write*. That was a reasonable prototype shortcut and is not
-- a reasonable thing to sell on top of.
--
-- Nothing legitimately needs it. Every caller is server-side holding the
-- service key: the Python API through its own client, and Conversation-Agent
-- through `getSupabase()`, whose own docstring says "Server-side Supabase
-- client". service_role bypasses RLS entirely, so revoking anon changes
-- nothing about how either service works. The only browser file that mentions
-- Supabase is `src/lib/firebase.ts`, an unused stub whose comment points at
-- the server module.
--
-- `voice_tenants`, `voice_calls`, `voice_knowledge` and `voice_messages` were
-- already closed by earlier migrations and are left alone here.
--
-- Safe to run twice. A table that does not exist is skipped rather than
-- failing the whole script.

do $$
declare
  target text;
  targets text[] := array[
    'calls',
    'campaigns',
    'coach_directives',
    'integration_configs',
    'meta_migrations',
    'organizations',
    'presence_events',
    'presence_state',
    'privacy_audit',
    'social_accounts',
    'social_messages',
    'social_threads',
    'whatsapp_accounts',
    'whatsapp_calling_configs',
    'whatsapp_calls',
    'whatsapp_messages',
    'whatsapp_threads'
  ];
begin
  foreach target in array targets loop
    -- Skip anything this project does not have, so the script is portable
    -- between the three Supabase projects.
    if to_regclass(format('public.%I', target)) is null then
      raise notice 'skipping %, which does not exist here', target;
      continue;
    end if;

    execute format('alter table public.%I enable row level security', target);

    -- The permissive policy from each table's first migration.
    execute format('drop policy if exists prototype_temp_anon_access on public.%I', target);

    -- No policy for anon or authenticated, on purpose: with RLS on and no
    -- policy that matches, those roles see nothing and can write nothing.
    execute format('drop policy if exists service_role_only on public.%I', target);
    execute format(
      'create policy service_role_only on public.%I for all to service_role '
      'using (true) with check (true)', target);

    -- Belt and braces. PostgREST checks column privileges as well as RLS, so
    -- revoking here means a misconfigured policy later cannot quietly
    -- re-expose the table.
    execute format('revoke all on public.%I from anon, authenticated', target);

    raise notice 'locked %', target;
  end loop;
end $$;

-- Anything created from here on inherits the same default rather than the
-- permissive one. Without this, the next table added by any service is public
-- again and nobody notices until someone checks.
alter default privileges in schema public revoke all on tables from anon, authenticated;
