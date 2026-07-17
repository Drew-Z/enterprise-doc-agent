# Backend Development Guidelines

These guidelines record the backend conventions proven by M0.

## Guidelines Index

| Guide | Scope | Status |
|---|---|---|
| [Directory Structure](./directory-structure.md) | Python package ownership | Adopted in M0 |
| [Database Guidelines](./database-guidelines.md) | SQLAlchemy and Alembic | Adopted in M0 |
| [Error Handling](./error-handling.md) | Health and process failures | Adopted in M0 |
| [Quality Guidelines](./quality-guidelines.md) | Ruff, mypy, pytest | Adopted in M0 |
| [Logging Guidelines](./logging-guidelines.md) | Secret-safe JSON logs | Adopted in M0 |
| [Multipart Upload Control Plane](./upload-control-plane.md) | Create, presign, resume, and reconciliation | Adopted in M1 Slice 5 |

Only implemented behavior belongs here. M1-M7 business patterns must be added after
their code and tests exist.

## Proven Examples

- `apps/api/src/enterprise_doc_api/app.py`
- `apps/worker/src/enterprise_doc_worker/app.py`
- `packages/core/src/enterprise_doc_core/health/models.py`
- `tests/foundation/test_repository_contract.py`
