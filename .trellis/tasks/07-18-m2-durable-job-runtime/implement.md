# M2 Durable Job Runtime Implementation Plan

## Slice 1: Schema and contracts

- [x] Add feature-owned `jobs/models.py` and exports for Job, JobAttempt, JobEvent, and
  OutboxEvent with explicit string statuses and database checks.
- [x] Register models in `db/metadata.py` and add
  `20260718_0006_durable_job_runtime.py`; test upgrade/downgrade and indexes.
- [x] Add typed service protocols/results for claim, heartbeat, success, failure,
  retry, cancel, and outbox publication.

## Slice 2: Red tests for state transitions

- [x] Idempotent creation and duplicate delivery.
- [x] Concurrent claim with `FOR UPDATE SKIP LOCKED`.
- [x] Lease expiry, abandoned attempt, new fencing token, and stale-write rejection.
- [x] Heartbeat, retry classification/backoff/jitter, dead/manual retry, cancel, and
  terminal guards.

## Slice 3: Green durable runtime

- [x] Implement repository/service transactions with bounded payload/error validation.
- [x] Implement outbox claim/publish/mark-published and repeat-safe delivery.
- [x] Add worker Celery adapter, stable-id task envelope, and graceful shutdown hooks.

## Slice 4: M1 atomic integration

- [x] Modify upload `_finalize_completion` to insert one ingestion Job and OutboxEvent in
  the existing transaction.
- [x] Add a PostgreSQL/MinIO concurrent completion test and completion replay assertions.
- [x] Update M1 model/migration contracts to assert historical scope without weakening
  their pinned revision expectations.

## Slice 5: Verification and evidence

- [x] Run formatting, lint, mypy, unit, integration, and existing M1 smoke/evidence
  contracts.
- [ ] Add an M2 machine-readable evidence manifest with exact commands, environment,
  commit SHA, artifact paths, and limitations.
- [x] Run `task.py validate 07-18-m2-durable-job-runtime`, update the interview Q&A with
  code-backed facts, then commit. Do not archive until all acceptance criteria are
  evidenced and reviewed.

## Review gates

- Every behavior slice starts with a failing test and ends with focused tests green.
- No claim of Celery delivery, worker pool scale, or production deployment without
  executable evidence.
- Keep M1 evidence immutable and preserve the parent dependency ordering.
