# M2 Acceptance Audit

## Implemented and locally verified

- Four tenant-scoped SQLAlchemy models and additive Alembic revision
  `20260718_0006`.
- Job idempotency, append-only attempts/events, atomic claim, lease, heartbeat,
  fencing-token checks, retry/dead/manual retry, cancel request, and terminal guards.
- Outbox lease claim and conditional mark-published; duplicate publication remains safe.
- Celery JSON envelope, late-ack/reject-on-worker-lost configuration, publisher loop,
  and worker shutdown signal.
- M1 completion transaction creates one ingestion Job and OutboxEvent with the
  DocumentVersion; concurrent MinIO completion test proves one durable set.

## Commands and results (before evidence commit)

- `uv run ruff format --check .` — 128 files already formatted.
- `uv run ruff check .` — passed.
- `uv run mypy packages/core/src apps/api/src apps/worker/src` — 67 source files passed.
- `uv run pytest -m "not integration" -q` — 217 passed, 58 deselected.
- `uv run pytest -m integration -q` — 58 passed, 214 deselected.
- `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build` — passed; frontend tests 124.
- `python .trellis/scripts/task.py validate 07-18-m2-durable-job-runtime` — passed.

## Limits

- Celery broker and publisher tests use a deterministic adapter; no remote broker or
  multi-process Worker throughput claim is made.
- M3 document parser/embedding handler is not implemented; M2 only creates and wakes
  `document.ingest` jobs.
- No Kubernetes, CD, production traffic, SLO, or capacity claim is made.
- M1 1 GiB evidence remains immutable and is not relabeled as M2 evidence.
