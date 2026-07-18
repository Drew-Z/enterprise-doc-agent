# M4 Agent MCP HITL: Technical Design

## Design Summary

M4 adds a controlled Agent execution layer above the archived M2 durable runtime and
M3 retrieval contracts:

```text
React run workspace
  |-- JWT/HTTPS --> FastAPI run, approval, artifact, and SSE APIs
  `-- direct download --> short-lived object-store URL

FastAPI
  |-- PostgreSQL AgentRun transaction
  |     `-- initial Job + Outbox + AgentRunExecution + AgentRunEvent
  `-- fetch-based SSE --> durable AgentRunEvent replay

Celery Worker
  |-- M2 claim / lease / heartbeat / fencing
  |-- fixed LangGraph workflow
  |-- official AsyncPostgresSaver checkpoint
  |-- ChatModelGateway
  `-- stdio MCP client --> enterprise-doc-mcp

MCP stdio server
  |-- signed per-run execution context
  |-- server-side membership/capability/target/approval checks
  |-- HybridRetrievalService
  `-- artifact object store + PostgreSQL audit rows
```

The API never executes the graph. A human approval never keeps a Worker lease open.
The initial execution segment ends successfully with `waiting_approval`; an approval
decision creates a new idempotent resume Job for the same LangGraph `thread_id`.

## Ownership And Layout

```text
packages/core/src/enterprise_doc_core/
  agents/
    models.py              # AgentRun, events, executions, evidence, approvals, tools, artifacts
    schemas.py             # internal typed state and public result contracts
    service.py             # create/status/cancel/event and transaction orchestration
    graph.py               # fixed LangGraph builder and node routing
    gateway.py             # model protocol, deterministic and OpenAI-compatible adapters
    checkpoint.py          # AsyncPostgresSaver lifecycle and setup contract
    policy.py              # capability, risk, approval, and injection-resistant policy
    tools.py               # server-side tool executor and idempotency
    artifacts.py           # deterministic object keys, verification, download authorization
    sse.py                 # public event serializer and replay cursor rules
  object_store/
    artifacts.py           # bounded put/head/presign-get adapter

apps/api/src/enterprise_doc_api/
  agents/router.py         # run/status/cancel/events/approval/artifact endpoints
  agents/schemas.py        # strict request/response models

apps/worker/src/enterprise_doc_worker/
  agents.py                # agent.execute handler and graph runtime wiring

apps/mcp/src/enterprise_doc_mcp/
  server.py                # stable MCP v1 FastMCP stdio server
  __main__.py              # enterprise-doc-mcp entry point

apps/web/src/agent/
  api/                     # Zod-validated HTTP and fetch-SSE client
  state/                   # reducer for run/event/reconnect/approval state
  components/              # document selector, run form, timeline, approval, artifact

tests/agent, tests/mcp, tests/security, evaluation, evidence/m4
```

Core owns business rules. API, Worker, MCP, and Web are adapters. API and Worker do
not import one another. The MCP app imports Core only.

## Dependency Decisions

- `langgraph>=1.2.9,<2`
- `langgraph-checkpoint-postgres>=3.1,<4`
- `mcp>=1.28.1,<2`; v2 is pre-release during M4 planning and is not selected.
- The official PostgreSQL checkpointer uses Psycopg 3 independently from the existing
  SQLAlchemy engine. A dedicated setup command calls `AsyncPostgresSaver.setup()`.
- `LANGGRAPH_STRICT_MSGPACK=true` is required and graph state is limited to simple
  JSON-compatible values and UUID strings. No arbitrary Python object, secret, signed
  URL, prompt body, document body, or raw model output is stored in a checkpoint.

## Data Model

All tables use UUID primary keys, timezone-aware timestamps, explicit string status
checks, tenant foreign keys, bounded JSON payloads, and named constraints/indexes.

### `agent_runs`

Key fields:

```text
id, tenant_id, actor_id, document_version_id
idempotency_key, request_fingerprint
task_type, input_text, extraction_schema, publish_requested
status, graph_thread_id, graph_version
prompt_version, model_provider, model_name, model_version
tool_schema_version, index_generation_id
next_event_seq, current_execution_seq
error_code, error_message
created_at, started_at, waiting_at, finished_at, cancelled_at
```

Status machine:

```text
pending -> running -> waiting_approval -> running
pending/running/waiting_approval -> cancelled
running -> succeeded | refused | failed
waiting_approval -> rejected | expired
```

Terminal states are `succeeded`, `refused`, `failed`, `cancelled`, `rejected`, and
`expired`. A checkpoint cannot move a terminal business row back to running.

### `agent_run_executions`

One row links each run segment to one M2 Job:

```text
id, tenant_id, run_id, sequence, job_id
kind(initial|resume), approval_request_id, resume_fingerprint
created_at
unique(run_id, sequence), unique(job_id)
```

The initial API transaction creates sequence 0. An approval decision creates at most
one resume execution for its decision fingerprint. Job attempts remain in M2 and are
not duplicated here.

### `agent_run_events`

```text
id, tenant_id, run_id, seq, event_type, event_version
public_payload, created_at
unique(run_id, seq)
```

The event service locks `AgentRun`, increments `next_event_seq`, and inserts the event
in the same transaction as the corresponding business state change. Public payloads
are allowlisted per event type; arbitrary model/tool payloads are rejected.

### `agent_run_evidence`

This freezes the exact authorized M3 candidates used by a run:

```text
id, tenant_id, run_id, chunk_id, document_version_id, generation_id
rank, rrf_score, content_sha256, created_at
unique(run_id, chunk_id), unique(run_id, rank)
```

The row contains identifiers and hashes, not document text. `read_chunk` may return
text only for a chunk already present in the run's evidence set.

### `approval_requests`

```text
id, tenant_id, run_id, requested_by_actor_id, decided_by_actor_id
operation, target_resource_type, target_resource_id
target_document_version_id, target_fingerprint
status, decision_idempotency_key, decision_comment
requested_at, expires_at, decided_at, consumed_at, revoked_at
```

Statuses are `pending`, `approved`, `rejected`, `expired`, `revoked`, and `consumed`.
Only an active owner membership can approve publication. Approval is not a bearer
token and is never accepted from model output; the server reloads the row under lock.

### `tool_executions`

```text
id, tenant_id, run_id, tool_name, capability
idempotency_key, request_fingerprint, input_sha256
target_resource_type, target_resource_id, target_version
approval_request_id, status, result_summary, error_code
created_at, started_at, finished_at
unique(tenant_id, idempotency_key)
```

Only hashes and bounded metadata are stored. Tool input/output bodies are not logged or
persisted in the audit row.

### `agent_artifacts`

```text
id, tenant_id, run_id, source_document_version_id
kind, status, content_type, object_bucket, object_key
content_sha256, size_bytes, behavior_versions
created_at, verified_at, published_at, revoked_at
```

Statuses are `writing`, `draft_ready`, `published`, `failed`, and `revoked`. The API
returns a download URL only for `draft_ready` artifacts from non-publish runs or
`published` artifacts from approval-required runs, after a fresh object `HEAD` matches
the stored size and SHA-256 metadata.

## Transaction Boundaries

### Create run

One SQLAlchemy transaction:

1. Revalidate active tenant membership and ready DocumentVersion ownership.
2. Lock by tenant/idempotency key and compare the request fingerprint.
3. Insert AgentRun and `run.created` event.
4. Call M2 `create_job_records()` for `agent.execute`.
5. Insert AgentRunExecution sequence 0.
6. Commit; the Outbox publisher wakes the Worker.

No model, retrieval, object, MCP, or graph call occurs in this transaction.

### Pause for approval

The graph creates the draft artifact and an ApprovalRequest through idempotent services
in nodes completed before the interrupt node. The interrupt node itself performs only
pure reads plus `interrupt(payload)`. When execution pauses, the handler records
`waiting_approval` and returns a successful segment outcome. A crash between checkpoint
and business update is repaired by replaying the same idempotent transition.

### Decide and resume

One transaction locks approval and run, revalidates owner membership, exact target
version/fingerprint, expiry, and terminal state, records the decision and event, then
creates one resume Job/Outbox/AgentRunExecution. The Worker invokes the same graph
thread with `Command(resume={approval_id, decision, decision_fingerprint})`.

### Publish

`publish_artifact` locks ToolExecution, ApprovalRequest, AgentArtifact, AgentRun, and
DocumentVersion in a stable order. It verifies the signed execution context and exact
target fingerprint, performs an idempotent object operation, verifies object metadata,
then marks approval consumed and artifact published. A retry returns the prior result.

## LangGraph Design

Graph state stores identifiers, decisions, versions, and hashes only:

```text
run_id, tenant_id, actor_id, document_version_id
task_type, publish_requested
evidence_ids, answer_artifact_id, approval_request_id
outcome, refusal_reason, graph_version
```

Nodes reload sensitive input/evidence from authorized PostgreSQL rows when needed.

```text
START
  -> load_run
  -> authorize
  -> retrieve_evidence
  -> [refusal -> finalize_refused -> END]
  -> generate_answer
  -> validate_answer
  -> create_draft
  -> assess_risk
  -> [no publish -> finalize_success -> END]
  -> create_approval
  -> approval_interrupt
  -> [rejected/expired -> finalize_rejected -> END]
  -> publish_artifact
  -> finalize_success
  -> END
```

Every node is re-entrant. External writes use stable idempotency keys. Code before
`interrupt()` is side-effect free because LangGraph restarts that node on resume.

`thread_id` is the AgentRun UUID. `checkpoint_ns` contains the graph version so a code
upgrade cannot silently resume incompatible state. Unknown graph/prompt/tool schema
versions fail with a stable manual-intervention error rather than guessing.

## Model Gateway

`ChatModelGateway.generate(request) -> GroundedModelOutput` is injected into graph
runtime. The request contains task type, bounded user input, authorized evidence, JSON
schema, and behavior versions. It never contains credentials, object keys, or approval
tokens.

Providers:

1. `DeterministicGroundedGateway`: local/test provider that produces a stable answer
   and citations from the authorized evidence list. It proves orchestration contracts,
   not semantic quality.
2. `OpenAICompatibleChatGateway`: calls `/chat/completions` with temperature 0, bounded
   timeout/output size, a strict JSON response contract, and no automatic tool execution.

The validated output includes answer text, structured fields, citation proposals, and
an optional risk hint. Server policy decides risk and publication independently. One
bounded repair request may be attempted for syntactic/schema failure; authorization,
citation, or policy failures are never repaired by asking the model to override them.

## MCP And Tool Authorization

The stable MCP v1 server runs on stdio. stdout is protocol-only; operational logs go to
stderr through the project JSON logger. The Worker launches the server with a short-
lived signed execution-context token in the process environment. The token is injected
by the client and is absent from the tool schema and model prompt.

Signed context fields:

```text
version, tenant_id, actor_id, run_id, execution_id
capabilities, target_document_version_id
approval_request_id when resuming publication
issued_at, expires_at, nonce
```

The server verifies HMAC signature and expiry, then reloads AgentRun, membership,
resource ownership, evidence, approval, and artifact rows. Client/model arguments never
override those trusted values.

Five tools:

| Tool | Capability | Side effect | Key authorization |
|---|---|---|---|
| `search_document` | read_evidence | freezes AgentRunEvidence rows | run document version only |
| `read_chunk` | read_evidence | none | chunk must be in run evidence set |
| `create_draft_artifact` | create_draft | object + draft metadata | validated answer for same run |
| `get_artifact` | read_artifact | none | same tenant/run and visible state |
| `publish_artifact` | publish | publishes exact artifact | owner approval exact target/fingerprint |

Each call requires a stable idempotency key generated by the Worker, a strict Pydantic
input model with extra fields forbidden, an `asyncio.timeout`, and a ToolExecution audit
row. Tool failures use stable public error codes and never reveal cross-tenant resource
existence.

## Prompt Injection Boundary

- User input, retrieved document text, model output, and MCP result text are untrusted.
- Evidence is serialized in a delimited data structure and never concatenated into the
  system instruction as executable text.
- The model has no credential, tenant, actor, approval, object key, or publish state.
- Publish capability exists only in a resume execution context minted after a valid
  owner approval.
- The tool server reloads all authorization from PostgreSQL. A model saying "approved"
  or returning a foreign tenant UUID has no effect.
- Security tests assert database/object side-effect counts, not merely a refusal string.

## SSE Contract

The public event envelope is:

```json
{
  "version": 1,
  "runId": "uuid",
  "seq": 12,
  "type": "approval.requested",
  "createdAt": "RFC3339",
  "payload": {"approvalId": "uuid", "operation": "publish_artifact"}
}
```

Wire format uses `id: <seq>`, `event: agent-run`, and JSON `data:`. The API parses
`Last-Event-ID` as a non-negative integer, queries `seq > cursor`, and continues polling
until disconnect. It emits SSE comments as heartbeats without consuming sequence
numbers. Since M4 retains all run events, there is no retention-gap response yet.

The Web client uses authenticated `fetch()` streaming rather than native EventSource,
stores the last validated sequence, ignores duplicate sequence numbers, rejects gaps,
and reconnects with exponential backoff bounded by the run terminal state.

## Failure Semantics

- Model timeout/429/5xx: retryable Job failure unless the configured segment budget is
  exhausted.
- Model 4xx contract/auth error, malformed run, wrong graph version: permanent/manual.
- Retrieval refusal: successful `refused` business outcome, not an infrastructure error.
- MCP missing/invalid context or policy denial: permanent security denial with no side
  effect; publish denial keeps the run waiting/rejected according to approval state.
- MCP transport/process crash before a side effect: retryable.
- Crash after side effect: ToolExecution/artifact idempotency returns the committed result.
- SSE disconnect: no business mutation; replay starts after the last consumed sequence.
- Cancel: current M2 Job is cancelled cooperatively; later graph/tool commits recheck run
  state and fencing so a stale segment cannot publish.

## Migration, Compatibility, And Rollback

- Add Alembic revision after `20260718_0008`; do not edit prior revisions.
- Business tables are Alembic-owned. Official LangGraph checkpoint tables are created by
  the explicit idempotent setup command and verified by a contract test.
- New event/model/tool schema fields are versioned. Readers reject unknown major versions.
- A rollback disables new run creation and MCP publication first, drains/cancels active
  runs, keeps business/checkpoint tables for forward recovery, and rolls back application
  code. The M4 migration downgrade is used only after all M4 rows/checkpoints are removed
  in a test environment; production rollback does not assume destructive downgrade.
- Existing M2/M3 Jobs, DocumentVersions, chunks, evidence, and manifests are unchanged.

## Evidence Contract

M4 writes immutable evidence under `evidence/m4/` and updates `evidence/index.json`.
The manifest uses the parent stable fields plus artifact SHA-256 values. It must record:

- locked dependency versions and checkpointer setup command;
- backend/frontend quality gates;
- PostgreSQL migration/checkpoint, graph recovery, MCP, approval, SSE, artifact, and
  security integration commands;
- deterministic Agent/safety evaluation results;
- Playwright happy, reconnect, unauthorized, and injection results;
- explicit limitations for deterministic model quality, local stdio MCP, local
  PostgreSQL/MinIO/Redis, no public deployment, and no capacity evidence.
