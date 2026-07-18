# M2 Durable Job Runtime

## Goal

Turn the uploaded `DocumentVersion` into a durable, retryable ingestion job without
making PostgreSQL depend on Celery/Redis delivery. The runtime must survive duplicate
delivery, worker crashes, stale leases, Redis outages, and lost acknowledgements while
preserving tenant ownership and an append-only execution history.

## Requirements

### Durable state

- Add tenant-scoped `Job`, append-only `JobAttempt`, append-only ordered `JobEvent`,
  and transactional `OutboxEvent` records.
- PostgreSQL is authoritative. Queue messages carry only stable identifiers and small
  routing metadata; no business result is inferred from Redis or a Celery ack.
- A `(tenant_id, idempotency_key)` identifies one effective job creation. Replays return
  the existing job and do not create another effective side effect.

### Claim and execution safety

- Claim is an atomic PostgreSQL transaction using row locking/`SKIP LOCKED`, a per-job
  lease, a unique lease token, and a monotonically increasing fencing token.
- Every attempt is append-only. A stale attempt may be marked `abandoned`, but it is
  never overwritten by a later attempt.
- Heartbeat and every success/failure/cancel write must condition on the current job,
  lease token, worker identity, and fencing token. A stale worker must be rejected.
- Lease expiry is reclaimable by another worker. Exhausted retryable work enters a
  durable `dead`/manual-intervention state.

### Retry, cancel, and publication

- Classify failures as retryable, permanent, or cancellation. Retryable failures use
  bounded exponential backoff with jitter; permanent failures and exhausted retries
  become `dead` with a stable error code.
- Provide durable retry and cancel transitions. Cancellation is idempotent and cannot
  turn a succeeded job back into a non-terminal state.
- Outbox publication leases rows, may publish repeatedly, and marks a row published
  only after delivery. Repeated publication must be harmless to the consumer.
- Worker shutdown stops new claims, allows bounded cleanup of current leases, and
  leaves unfinished work recoverable by lease expiry.

### M1 integration boundary

- Multipart completion must create the `DocumentVersion`, ingestion `Job`, and initial
  `OutboxEvent` in the same PostgreSQL transaction that converts the upload reservation
  to used storage and marks the session completed.
- A completion replay returns the same durable document/version/job identifiers and does
  not enqueue a second job.

## Acceptance Criteria

- [ ] Four durable models are registered in SQLAlchemy metadata and have an Alembic
  migration with tenant ownership, status checks, stable indexes, and downgrade tests.
- [ ] Unit tests prove idempotent job creation, duplicate delivery with one effective
  claim, concurrent claim serialization, lease expiry/reclaim, heartbeat renewal, and
  stale fencing rejection.
- [ ] Unit tests prove retryable/permanent/cancelled classification, backoff bounds and
  jitter injection, dead/manual retry, idempotent cancel, and terminal-state guards.
- [ ] Outbox tests prove transactional creation, repeat publication, lease recovery, and
  idempotent consumer delivery.
- [ ] Worker lifecycle tests prove shutdown prevents new claims and an interrupted
  attempt is reclaimable after its lease expires.
- [ ] PostgreSQL integration tests prove two concurrent upload completions create one
  `DocumentVersion`, one `Job`, one `OutboxEvent`, and one quota conversion; replay is
  stable.
- [ ] Existing M1 unit, integration, smoke, evidence-contract, and Trellis validation
  gates remain green; M1 evidence files are unchanged.
- [ ] The M2 interview document is updated only with code-backed facts and labels
  unimplemented production claims explicitly.

## Constraints

- Do not edit applied migrations or rewrite M1 evidence.
- Keep business state in `packages/core`; API and Worker remain adapters and must not
  import one another.
- Keep payloads bounded and redact lease tokens, object keys, filenames, document text,
  and credentials from logs.
