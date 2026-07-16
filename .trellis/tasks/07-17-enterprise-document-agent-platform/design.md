# Enterprise Document Agent Platform: Parent Design

## Architecture Shape

The project is a modular monorepo with separately runnable Web, API, Worker, and MCP processes sharing explicit domain contracts.

```text
React Web
  ├─ HTTPS/JWT → FastAPI API
  └─ presigned Multipart → MinIO/S3

FastAPI API
  ├─ PostgreSQL + pgvector (business truth)
  ├─ Redis (rate limits and queue broker)
  ├─ Outbox publisher → Celery
  └─ SSE events → Web

Python Workers
  ├─ atomic Job claim / lease / heartbeat / fencing
  ├─ document ingestion and retrieval
  ├─ LangGraph workflow and checkpoint
  ├─ model gateway
  └─ object-store artifacts

MCP Server
  └─ tenant-aware read, draft, and approval-bound publish tools
```

## Boundary Decisions

### PostgreSQL is authoritative

Jobs, attempts, events, approvals, document versions, Agent runs, artifacts, and audit records are committed in PostgreSQL. Redis queue state is never used to answer whether a business operation succeeded.

### Queue messages contain identifiers

Celery payloads carry stable identifiers and small routing metadata. Large documents, full prompts, and durable state remain in PostgreSQL or object storage.

### File transfer bypasses application data plane

The API creates and authorizes upload sessions. Browser clients upload parts directly to MinIO/S3 through short-lived presigned URLs. The API never buffers the complete file.

### Business lifecycle and graph checkpoint are distinct

`Job`/`AgentRun` represent user-visible lifecycle and authorization. LangGraph checkpoints represent node-level recovery. A checkpoint cannot authorize a transition that the business row rejects.

### Tools are server capabilities

Model output only proposes tool calls. The MCP/tool execution layer validates principal, tenant, target resource, capability, schema, timeout, idempotency, and approval.

## Cross-Milestone Contracts

### Identity context

Every authenticated business request, job, run, tool call, event, artifact, and trace carries:

```text
request_id
tenant_id
actor_id
correlation_id
idempotency_key where applicable
```

Infrastructure health endpoints carry request/correlation identifiers but are not assigned fabricated tenant or actor identities. M0 provides the principal-enrichment boundary; M1 must introduce real principal resolution before the first tenant-scoped business endpoint.

### Event contract

Run events use a versioned envelope with monotonically increasing `seq`. Clients can reconnect with the last consumed sequence. Sensitive tool inputs and raw model output are not event payloads.

### Version contract

Each run records code/image version plus graph, prompt, model, embedding, tool-schema, and index versions. Rollback and eval compare the complete behavior version, not only the application commit.

## Reliability Invariants

1. A worker may submit state only while holding the current lease token.
2. An idempotency key maps to one effective business operation per tenant.
3. Outbox publication may repeat; consumers must be idempotent.
4. Attempts are append-only and never overwrite previous execution evidence.
5. An artifact becomes downloadable only after database state and object metadata are verified.
6. Human approval is bound to actor, operation, target resource, version, and expiry.

## Security Invariants

1. Tenant filters are applied before retrieval and tool execution.
2. Retrieved text is evidence, never instruction.
3. No model can directly set accepted, approved, published, or completed business state.
4. Logs and traces default to metadata, hashes, sizes, versions, and error classes rather than document bodies.
5. External URLs and remote MCP targets use network allowlists and SSRF controls when introduced.

## Rollout Shape

- M0-M4 produce a local interview-ready system.
- M5 establishes measured reliability and quality evidence.
- M6 turns the project into an actual delivery pipeline and staging deployment.
- M7 is optional for environments without suitable GPU capacity, but the model gateway must exist before it.

Database changes use expand/migrate/contract. Deployments use immutable image digests. Rollback never assumes destructive schema changes can be undone automatically.

## Evidence Contract

Each child task writes immutable manifests under `evidence/<milestone>/<YYYYMMDD-HHMMSS>-<evidence_id>.json`, stores referenced artifacts under the same milestone directory, and updates the tracked parent index at `evidence/index.json`. The stable fields are:

```text
evidence_id, milestone, requirement_ids, status
command_or_procedure, environment
commit_sha, image_digest where applicable
started_at, completed_at, result_summary
artifacts, limitations, owner
```

Allowed status values are `passed`, `failed`, and `blocked_external`. `blocked_external` requires a linked manual-gate record and never counts as passed. Manual-gate records contain `gate_id`, `requirement`, `owner`, `blocking_reason`, `prerequisites`, `required_evidence`, `state`, and `review_date`. Child tasks finalize exact commands and paths after their executable interfaces exist; parent integration rejects placeholders.

Production promotion additionally requires an evidence index for secret handling, TLS/ingress and network isolation, least-privilege service identities, migration compatibility, database backup/restore, immutable images and SBOM/scans, audit access, monitoring/alerting, incident response, and rollback runbooks.

## Bootstrap Spec Strategy

The repository currently has no project-specific implementation patterns. Generated Trellis spec files are bootstrap placeholders, not evidence of adopted conventions. The bootstrap task must not be completed with aspirational rules. M0 establishes the first real directory, testing, configuration, logging, and API patterns; then those facts are written into `.trellis/spec/` and the bootstrap task is archived.
