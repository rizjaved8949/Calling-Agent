-- Let a company keep more than one document.
--
-- `voice_knowledge` was created with a **unique** index on `tenant_id` and the
-- comment "One document per tenant", which fitted the service that made it:
-- one knowledge-base PDF per number.
--
-- A company has more than one kind of material — a price list, an FAQ, a
-- prospectus, a term's timetable — and wants to replace one without losing the
-- others. With the unique index, uploading a second returns
--
--   23505 duplicate key value violates unique constraint
--         "voice_knowledge_tenant_idx"
--
-- which is the upload failing for a reason the person uploading cannot act on.
--
-- Replacing a document by name still works: the row id is derived from the
-- tenant and the document name, so re-uploading "Prices 2026" overwrites
-- rather than accumulating.
--
-- Safe to run twice.

drop index if exists public.voice_knowledge_tenant_idx;

-- The lookup it was there for — every document for one company — is still
-- worth an index. It just is not unique.
create index if not exists voice_knowledge_tenant_idx
  on public.voice_knowledge (tenant_id);

-- Newest first, which is the order documents are offered to the agent in.
create index if not exists voice_knowledge_updated_idx
  on public.voice_knowledge (tenant_id, updated_at desc);
