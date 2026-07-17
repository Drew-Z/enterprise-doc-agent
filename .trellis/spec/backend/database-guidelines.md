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

## Proven Examples

- `packages/core/src/enterprise_doc_core/db/engine.py`
- `packages/core/src/enterprise_doc_core/health/adapters.py`
- `packages/core/src/enterprise_doc_core/db/migrations/versions/20260717_0001_enable_vector.py`
- `tests/foundation/test_migration_contract.py`
