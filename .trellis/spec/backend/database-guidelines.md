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

## Proven Examples

- `infra/k8s/overlays/single-node-4c4g/resources-patch.yaml`
- `tests/deployment/test_m6_contracts.py::test_single_node_4c4g_overlay_matches_current_server_envelope`

- `packages/core/src/enterprise_doc_core/db/engine.py`
- `packages/core/src/enterprise_doc_core/health/adapters.py`
- `packages/core/src/enterprise_doc_core/db/migrations/versions/20260717_0001_enable_vector.py`
- `tests/foundation/test_migration_contract.py`
