# Database Guidelines

## Current Contract

M0 uses SQLAlchemy 2 async engines for runtime checks and Alembic for schema
ownership. PostgreSQL is the future business source of truth; Redis and MinIO are
foundation dependencies, not authoritative business stores.

`create_database_engine` receives typed `DatabaseSettings`, enables
`pool_pre_ping`, and applies the configured connection timeout. Windows entry
points select an asyncio-compatible event loop before psycopg is used.

## Query Patterns

M0 readiness executes only a bounded `SELECT 1` through an injected
`DatabaseChecker`. New business queries must live in feature-owned modules and
receive explicit settings/session dependencies.

## Migrations

Run migrations from the repository root:

```powershell
uv run alembic upgrade head
uv run alembic downgrade base
uv run alembic upgrade head
```

Applied revisions are immutable. The M0 revision creates exactly the `vector`
extension and no business tables.

## Naming

Alembic revision files use `YYYYMMDD_sequence_description.py`. Future table,
column, constraint, and index naming must be introduced with the first real
business schema and then recorded here.

## Scenario: API Connection Budget and Read-only Diagnosis

### 1. Scope / Trigger

Concurrent API reads queue behind the 4C4G single-connection pool.

### 2. Signatures

`DatabaseSettings.pool_size`, `max_overflow` and `create_database_engine(settings)`;
Deployment `env` takes precedence over the shared ConfigMap.

### 3. Contracts

For the 4C4G profile, the API overrides `DATABASE__POOL_SIZE=4` and
`DATABASE__MAX_OVERFLOW=0` in its Deployment. Worker/Consumer keep the shared
single-connection setting and existing execution concurrency. Check effective
container environment after Kustomize merging, not just the ConfigMap. Resource
limits remain unchanged; this is a candidate connection budget, not capacity approval.

### 4. Validation & Error Matrix

Read-only assertion failure or actual database project mismatch aborts diagnosis before
business queries. Pool exhaustion follows the typed 503 contract in `error-handling.md`.

### 5. Good / Base / Bad Cases

When diagnosing latency, distinguish PostgreSQL execution time from client SQL
round trips, connection hold time and pool queueing. Counted SELECT reductions do
not establish an HTTP latency SLO. A read-only isolated ASGI process does not prove
serving-process performance. Enforce and verify transaction read-only state before
such probes: the deployed pooler ignored a startup `options` attempt. Do not silently
continue after that check fails or query another project listed by a connector.

### 6. Tests Required

Render the 4C4G overlay and assert effective API pool 4/0, Worker/Consumer 1/0. Release
tests must cover the declared API-only change and full original-template restoration.

### 7. Wrong vs Correct

Wrong: increase the shared pool and all background concurrency from an isolated result.
Correct: keep each process budget explicit, then measure the deployed HTTP workload.

## Initial Durable Job Records

`create_job_records` keeps the existing idempotency SELECT and fingerprint check.
For a new job, insert Job, seq-1 JobEvent, AuditEvent and optional OutboxEvent in
one PostgreSQL data-modifying CTE statement. Each child references the new Job's
RETURNING ID. Supply a fresh UUID for every inserted row: multiple client-side
UUID defaults can collide as bind parameters in a multi-table CTE. Keep audit
`event_metadata` mapped to its physical `metadata` column and retain database
timestamp/schema defaults.

All writes belong to the caller's transaction; do not commit within creation.
Flush pending caller-owned parents before the statement, including when the caller
disabled autoflush. Preserve Tenant/UploadSession lock ordering, replay behavior,
event/audit payloads and later job lifecycle operations. No-outbox jobs must still
receive their initial event and audit record. Do not treat a local query-count
reduction as proof of deployed HTTP latency.

PostgreSQL integration tests must count every SQL statement (including WITH),
verify the two-round-trip budget for a new job with already-flushed parents,
check original records on replay/conflict, and reject each table's write using a
real constraint. Verify no partial group survives failure or caller rollback,
and that repeated statements allocate distinct row IDs. Use an owned
loopback schema; never migrate shared public tables to run these tests.

## Provider Dispatch Metering

`ProviderCallService.begin` preserves business guard locks before tenant/day and
operation advisory locks. A materialized day-lock CTE feeds the operation-lock
SELECT, enforcing their order in one round trip. Daily and operation counts share
the next SELECT as independent scalar subqueries, preserving their indexed filters
and daily-limit error priority. **Never put counts in the lock statement:** its
READ COMMITTED snapshot precedes any lock wait and may miss the dispatch just
committed by the previous lock owner. Commit the intent before HTTP dispatch.

`finish` conditionally updates the scoped receipt only while `state='dispatched'`,
using `UPDATE ... RETURNING id`. PostgreSQL rechecks this predicate after a row-lock
wait. The first committed terminal state and all of its metadata remain unchanged
on replay. An unmatched update needs a scoped existence query: an absent receipt
raises `provider_usage_receipt_missing`; an existing terminal receipt is a no-op.
Constraint/driver errors retain `provider_usage_unavailable` and transaction rollback.
Cost and currency stay NULL without a versioned tariff.

Real isolated PostgreSQL tests exercise `recorded_post` with a no-query guard and
count all statements: normal dispatch plus finish uses at most five. Verify the
intent is visible to a separate connection before HTTP. Queue contenders behind
a held day lock to test both last daily and operation slots, then independently
hold the operation lock to prove day-before-operation ordering. Verify concurrent
finish, identity mismatch, constraint rollback and lost commit acknowledgment
without overwriting terminal metadata. Only the provider HTTP and database fault
injection boundaries are replaced. SQL savings do not establish deployed latency.

## New Usage Reservation Writes

After the existing Tenant, reservation and entitlement locks and expiry processing,
new Presales usage reservations combine the entitlement counter UPDATE and reservation
INSERT in one statement. The INSERT references the UPDATE CTE's returned entitlement
ID; retain the original clock/TTL values and database defaults. Flush pending expiry
events and caller-owned changes first, including under `session.no_autoflush`.
Do not commit within a caller-owned transaction.

The CTE's counter update bypasses ORM synchronization. After successful execution,
use `set_committed_value` for the locked entitlement's new reserved counter and the
database-returned `updated_at`; otherwise same-session reserve/settle/release can
read a stale counter or flush a duplicate UPDATE. This marks the ORM state as flushed,
not the database transaction as committed. Constraint failure must roll back both
writes; same-operation replay retains the original ID and expiry after uncertain commit.

Presales adds its new attempt to the session only after usage reservation and queue
deadline clipping. Keep the final source recheck and transaction boundary; the receipt
must see a durable Job, attempt and reservation with the final stored deadline.
Do not let an earlier quota lookup autoflush a provisional attempt then update it again.

Tests count all SQL through public reservation and single/batch receipt interfaces:
at most six for a new standalone reservation and seventeen for the configured admission
fixture. Verify same-session multiple operations, pending caller changes, expired
reservation release, both table constraint failures, caller rollback, commit acknowledgment
loss, same-key contention and last-slot contention behind a real Tenant lock. These
local query budgets do not prove public latency or production capacity.

## Proven Examples

- `infra/k8s/overlays/single-node-4c4g/resources-patch.yaml`
- `tests/deployment/test_m6_contracts.py::test_single_node_4c4g_overlay_matches_current_server_envelope`

- `packages/core/src/enterprise_doc_core/db/engine.py`
- `packages/core/src/enterprise_doc_core/health/adapters.py`
- `packages/core/src/enterprise_doc_core/db/migrations/versions/20260717_0001_enable_vector.py`
- `tests/foundation/test_migration_contract.py`
- `packages/core/src/enterprise_doc_core/jobs/service.py::create_job_records`
- `tests/jobs/test_job_creation_batch_integration.py`
- `tests/billing/test_provider_dispatch_roundtrips_integration.py`
- `tests/billing/test_reservation_batch_integration.py`
- `tests/presales/test_presales_admission_batch_integration.py`
