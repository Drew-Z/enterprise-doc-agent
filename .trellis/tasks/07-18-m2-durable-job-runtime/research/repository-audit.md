# M2 Repository Audit

## Existing extension points

- `packages/core/src/enterprise_doc_core/db/metadata.py` explicitly registers every
  model used by Alembic. M2 adds four feature-owned models there; Alembic already imports
  this metadata.
- Existing status fields use Python `StrEnum`, `String`, and database check constraints
  rather than PostgreSQL enum types.
- The current migration head is `20260717_0005`; M2 uses an additive
  `20260718_0006_durable_job_runtime.py` revision and must not edit M1 migrations.
- Upload completion finalizes `Document`, `DocumentVersion`, quota, and session state in
  `_finalize_completion`. Job and outbox creation must be inserted into this exact
  transaction.
- Worker currently has a probe server and shutdown event, but no Celery application,
  durable claim loop, outbox publisher, or executor.

## Test compatibility risks

- `packages/core/tests/test_upload_models.py` currently treats the M1 metadata table set
  as exhaustive. M2 must pin M1 expectations to the historical model subset and add an
  independent M2 model contract.
- `tests/multipart/test_m1_migration.py` upgrades to `head` while expecting only M1
  tables. It must target revision `20260717_0005`; a new M2 migration test owns `head`.
- Existing M1 evidence artifacts are immutable. New M2 evidence is stored separately.

## Decisions

- Use PostgreSQL row locking and conditional writes as the correctness mechanism;
  Celery acknowledgement options are delivery tuning, not durable-state authority.
- Keep queue messages bounded to stable UUID identifiers.
- Treat duplicate publication and duplicate task delivery as normal recovery paths.
- Allocate monotonic JobEvent sequence numbers while holding the Job row lock.
- Reject stale worker heartbeat/result writes by lease token plus fencing token.
