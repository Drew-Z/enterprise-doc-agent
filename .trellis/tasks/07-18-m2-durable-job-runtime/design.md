# M2 Durable Job Runtime Design

## Boundaries

`packages/core` owns the job state machine, SQLAlchemy models, repository/service
protocols, retry policy, and outbox claim transitions. `apps/worker` owns Celery/Redis
adapters, the publisher loop, process lifecycle, and the execution callback. `apps/api`
owns authenticated HTTP commands for status/retry/cancel only when those endpoints are
needed by the existing API surface. Neither app imports the other.

PostgreSQL is the source of truth. Celery receives `{job_id, tenant_id, event_id}` and
wakes a worker; the worker must re-read and claim the durable row before doing work.

## State model

Job states are `pending`, `running`, `retry_wait`, `succeeded`, `dead`, and `cancelled`.
Only `pending`/`retry_wait` are claimable. `running` with an expired lease is reclaimable
by the claim transaction. `succeeded`, `dead`, and `cancelled` are terminal. Manual retry
transitions `dead` or a failed `retry_wait` row to `pending` with an explicit actor and
new idempotency-safe event; it never resets the append-only attempts.

Each claim increments `fencing_token`, increments `attempts`, creates an attempt row,
assigns a random `lease_token`, and sets `locked_by`, `lease_expires_at`, and
`heartbeat_at`. A stale attempt is marked `abandoned` before a replacement attempt is
created. All writes from an executor use a conditional `UPDATE ... WHERE id, status,
locked_by, lease_token, fencing_token`; zero rows means the worker lost its lease.

Job events use a per-job monotonic `seq` allocated while holding the job row lock. The
event payload is versioned and excludes raw document/prompt content. Attempts retain
error class/code, timestamps, worker id, lease/fencing values, and a bounded error
message for audit and debugging.

## Outbox protocol

The upload completion transaction inserts the Job and an `OutboxEvent` whose payload is
only stable identifiers (`job_id`, `tenant_id`, `document_version_id`, `event_type`,
`payload_version`). A publisher claims pending or expired-publishing rows with a short
lease, commits the claim, publishes the Celery message, then conditionally marks the row
published. A crash between publish and mark causes a duplicate message, which is
expected and harmless because claim is idempotent.

## Retry and cancellation

Retry classification is explicit (`retryable`, `permanent`, `cancelled`). A retryable
failure before `max_attempts` creates a new availability time using bounded exponential
backoff plus injected jitter. Permanent or exhausted failures set `dead`. A cancel
command is idempotent: pending/retry-wait becomes `cancelled`; running records a cancel
request and the current worker observes it at heartbeat/checkpoint boundaries. Terminal
jobs reject later success/failure/cancel transitions.

## Upload completion transaction

Extend `_finalize_completion` in `packages/core/.../uploads/session_service.py` after the
`DocumentVersion` is flushed and before the transaction exits. Insert the ingestion Job
and OutboxEvent using the preallocated version id and the same tenant/actor/request
context. On completion replay, load the durable job through the version/session link and
return its id without inserting anything. This keeps PostgreSQL atomic while retaining
the existing S3 reconciliation behavior.

## Worker lifecycle

The worker process starts its probe server and publisher/consumer loops, then accepts
SIGTERM/SIGINT. Shutdown flips a stop event, prevents new claims, asks current tasks to
stop at a bounded deadline, and exits without force-marking running jobs successful.
Unfinished leases expire and are reclaimed. Redis/Celery connection errors are logged as
dependency failures and do not mutate durable job state.

## Compatibility and rollback

The migration is additive. Existing M1 rows remain valid. The upload finalization code
must be deployed only after the additive tables exist; rollback of application code may
leave already-created jobs/outbox rows, which older code can ignore. Never downgrade a
database that contains M2 rows without an explicit data-retention decision.
