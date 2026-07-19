# Backend Development Guidelines

These guidelines record backend conventions proven by implemented milestones.

## Guidelines Index

| Guide | Scope | Status |
|---|---|---|
| [Directory Structure](./directory-structure.md) | Python package ownership | Adopted in M0 |
| [Database Guidelines](./database-guidelines.md) | SQLAlchemy and Alembic | Adopted in M0 |
| [Error Handling](./error-handling.md) | Health and process failures | Adopted in M0 |
| [Quality Guidelines](./quality-guidelines.md) | Ruff, mypy, pytest | Adopted in M0 |
| [Logging Guidelines](./logging-guidelines.md) | Secret-safe JSON logs | Adopted in M0 |
| [Multipart Upload Control Plane](./upload-control-plane.md) | Create, presign, resume, and reconciliation | Adopted in M1 Slice 5 |
| [Multipart Upload Completion](./upload-completion.md) | Completion saga, envelope validation, and quota finalization | Adopted in M1 Slice 6 |
| [Multipart Operations And Evidence](./multipart-operations.md) | Restart/resume smoke, CI, RSS, and immutable evidence boundary | Adopted in M1 Slice 10 |
| [Agent MCP HITL](./agent-mcp-hitl.md) | LangGraph, MCP, approval, SSE and artifact contracts | Adopted in M4 |
| [Observability Evaluation And Load](./observability-eval-load.md) | Metrics, fault injection, eval and bounded load reports | Adopted in M5 worktree |
| [CI/CD And Kubernetes](./cicd-kubernetes.md) | Images, manifests, supply chain, backup and rollback | Adopted in M6 worktree |
| [Model Routing](./model-routing.md) | Fallback, circuit breaking, route identity and embedding dimensions | Adopted in M7 worktree |

Only implemented behavior belongs here. Worktree-adopted M5-M7 patterns are not
reviewed release or external deployment evidence.

## Proven Examples

- `apps/api/src/enterprise_doc_api/app.py`
- `apps/worker/src/enterprise_doc_worker/app.py`
- `packages/core/src/enterprise_doc_core/health/models.py`
- `tests/foundation/test_repository_contract.py`
