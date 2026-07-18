# M3 Repository Audit

- `DocumentVersion` currently has only `uploaded`, `ready`, and `failed` statuses and
  stores object identity in `documents.models`; M3 must preserve those transitions.
- `documents.envelope` already validates bounded PDF/TXT/DOCX envelopes but does not
  extract text; parsing must build on that trust boundary rather than duplicate it.
- The object-store protocol offers `head_object` and bounded `get_range`, not a raw
  streaming body. M3 should spool fixed ranges and keep a maximum byte budget.
- Migration `20260717_0001` enables pgvector extension; current database uses pgvector
  0.8.3. New schema must be additive and use the repository naming convention.
- M2's `JobDeliveryConsumer` accepts an injected async handler, so M3 can register a
  document-ingest handler without making API import Worker.
- Existing M1 evidence and tests are historical contracts; do not relabel them as M3
  parsing or retrieval evidence.
