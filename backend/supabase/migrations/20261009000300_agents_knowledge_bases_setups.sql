-- Agents, knowledge bases, and the routing between them.
--
-- Until now a company had exactly one of everything: one persona on its tenant
-- row, and one flat pile of documents its agent read for every call. These
-- three tables are what let a company have several — a different knowledge
-- base for the sales list than for the support line, answered by a different
-- agent, chosen per number and per direction.
--
-- The resolution chain a call walks to find its knowledge is, highest first:
--   1. the call setup matched for that number and direction  (resolvedBy=setup)
--   2. a knowledge base named explicitly on the request      (resolvedBy=explicit)
--   3. the agent's own default knowledge base                (resolvedBy=agent)
--   4. everything the company has uploaded                   (resolvedBy=company)
-- The last step is what keeps every existing company working unchanged: one
-- that never creates a knowledge base still answers from all of its documents.
--
-- Same (id, tenant_id, data jsonb) shape as the sibling tables, and locked to
-- service_role from the start, same as voice_campaigns.

create table if not exists public.voice_knowledge_bases (
  id text primary key,
  tenant_id text,
  data jsonb not null,
  updated_at timestamptz not null default now()
);

create table if not exists public.voice_agents (
  id text primary key,
  tenant_id text,
  data jsonb not null,
  updated_at timestamptz not null default now()
);

create table if not exists public.voice_call_setups (
  id text primary key,
  tenant_id text,
  data jsonb not null,
  updated_at timestamptz not null default now()
);

-- Every read is "this company, newest first".
create index if not exists voice_knowledge_bases_tenant_idx
  on public.voice_knowledge_bases (tenant_id, updated_at desc);
create index if not exists voice_agents_tenant_idx
  on public.voice_agents (tenant_id, updated_at desc);
create index if not exists voice_call_setups_tenant_idx
  on public.voice_call_setups (tenant_id, updated_at desc);

-- Documents already live in voice_knowledge. They gain a knowledge base id in
-- their jsonb rather than a column: a document with none belongs to the
-- company's whole pile, which is exactly how every existing row should behave.
create index if not exists voice_knowledge_kb_idx
  on public.voice_knowledge (tenant_id, (data ->> 'knowledgeBaseId'));

do $$
declare
  t text;
begin
  foreach t in array array[
    'voice_knowledge_bases', 'voice_agents', 'voice_call_setups'
  ] loop
    execute format('alter table public.%I enable row level security', t);
    -- No policy for anon or authenticated on purpose: with RLS on and nothing
    -- matching, those roles see nothing and can write nothing.
    execute format('drop policy if exists service_role_only on public.%I', t);
    execute format(
      'create policy service_role_only on public.%I '
      'for all to service_role using (true) with check (true)', t
    );
    execute format('revoke all on public.%I from anon, authenticated', t);
  end loop;
end $$;
