# M4 Repository Audit

Captured: 2026-07-18

## Reusable Contracts

### Identity and API

- `PrincipalContext` in `packages/core/src/enterprise_doc_core/context/request.py`
  carries trusted `tenant_id`, `actor_id`, and role.
- `DatabasePrincipalResolver.resolve()` in
  `packages/core/src/enterprise_doc_core/identity/jwt.py` reloads active User,
  Membership, and Tenant rows before returning the principal.
- Job routes in `apps/api/src/enterprise_doc_api/jobs/router.py` already demonstrate
  tenant-scoped reads and actor-aware retry/cancel writes.
- `apps/api/src/enterprise_doc_api/app.py` injects services through `app.state`; M4
  should follow that factory pattern rather than create global clients at import time.

### Durable jobs and events

- `Job`, `JobAttempt`, `JobEvent`, and `OutboxEvent` live in
  `packages/core/src/enterprise_doc_core/jobs/models.py`.
- `JobRuntimeService` already owns create, claim, heartbeat, succeed, fail, cancel,
  retry, status, attempt, and event transactions.
- `create_job_records()` can participate in a caller-owned SQLAlchemy transaction,
  which is required for AgentRun + initial Job + Outbox atomic creation.
- `JobDeliveryConsumer` in `apps/worker/src/enterprise_doc_worker/queue.py` runs a
  handler beside a real heartbeat/cancellation monitor and applies fencing-aware final
  writes. The new `agent.execute` handler should plug into this consumer.

### Retrieval and citations

- `HybridRetrievalService.retrieve()` in
  `packages/core/src/enterprise_doc_core/documents/retrieval_service.py` already
  enforces tenant, exact document version, generation consistency, and
  ready/succeeded/active generation filters.
- `decide_retrieval()` and `validate_citations()` in
  `packages/core/src/enterprise_doc_core/documents/retrieval.py` provide the refusal
  and citation authorization boundary M4 must reuse.
- M3 has no answer generator, reranker, actor/document ACL, or model gateway. M4 must
  not describe those as existing dependencies.

### Object store, logging, and database

- `S3MultipartObjectStore` already wraps blocking boto3 calls through an async boundary
  and provides `head_object`/`delete_object`; M4 needs a separate artifact protocol for
  put and presigned GET.
- `create_session_factory()` provides the SQLAlchemy async transaction boundary.
- `JsonFormatter` and `sanitize_log_value()` already redact token/secret/object fields.
  M4 must add prompt, raw model, tool input/output, approval context, and signed download
  fields to the explicit sensitive contract.
- Request/correlation IDs and OTel request attributes already exist. Durable AgentRun
  events remain PostgreSQL business records, not trace spans.

## Missing Surfaces

- No Chat/LLM model gateway or answer schema.
- No AgentRun, graph checkpoint business link, approval, tool audit, or artifact tables.
- No `StreamingResponse`, SSE cursor, WebSocket, or replay implementation.
- No MCP package, server, client, registry, or capability policy.
- No approval interrupt/resume behavior.
- No artifact put/presign/download API.
- No ready DocumentVersion listing endpoint for an Agent UI.
- No Web run workspace.

## Design Consequences

1. Reuse M2 Job segments rather than extending Job with an indefinite human-wait state.
2. Keep AgentRun events separate from JobEvent so the public SSE envelope can be strict,
   redacted, and versioned without exposing Worker audit payloads.
3. Freeze exact retrieval candidates in AgentRunEvidence before generation.
4. Use the existing tenant-wide membership policy for M4, add owner-only publication,
   and state clearly that per-document ACL/ABAC remains future work.
5. Add a separate MCP app to preserve API/Worker/Core ownership boundaries.
6. Keep checkpoints identifier-only; reload prompt/evidence under authorization inside
   each node.

## Known Risks

- Official LangGraph checkpointer tables are not Alembic-owned, so setup/check commands
  and Worker readiness contracts must make their lifecycle explicit.
- PostgreSQL checkpoint writes and SQLAlchemy business writes cannot share one atomic
  transaction. Re-entry and repair depend on stable idempotency keys and state checks.
- Stdio MCP has no browser/API principal. A signed per-run execution context must be
  injected by the trusted Worker and revalidated by the server.
- Fetch-based SSE needs a tested parser because native EventSource cannot attach the
  existing bearer header.
- Artifact object and database writes are cross-system. Deterministic keys, metadata
  verification, visibility state, and cleanup are required; exactly-once is not claimed.
