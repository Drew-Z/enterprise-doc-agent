# M4 Agent MCP HITL

## Goal

Turn the tenant-scoped, durable M2/M3 data path into one usable grounded Agent
workflow. An API request creates a run quickly, a Worker executes a fixed LangGraph
workflow from a PostgreSQL checkpoint, evidence and citations are validated on the
server, high-risk publication pauses for an exact-target human approval, and the
result becomes an auditable downloadable artifact.

The milestone is deliberately controlled rather than an unconstrained ReAct agent.
Model output may propose content or a tool intent, but it cannot set business state,
choose a tenant, broaden a document scope, or bypass an approval.

## Requirements

### Product and API

- **M4-R1**: `POST /api/agent-runs` accepts a ready `DocumentVersion`, task type
  (`qa`, `summary`, or `structured_extract`), bounded user input, and an optional
  publication request. It returns `202` with a stable `runId` and `jobId` without
  calling a model, parser, retrieval query, or tool in the request process.
- **M4-R2**: The create endpoint is idempotent by `(tenant_id, Idempotency-Key)` and
  a canonical request fingerprint. Same key and payload replays the same run and
  effective job; same key with a different payload returns a typed conflict. A key is
  never shared across tenants.
- **M4-R3**: Authenticated callers can read run status, ordered events, pending
  approvals, and artifacts only for their tenant. Owners may approve publication;
  members may read and create runs but cannot publish without an owner decision.
- **M4-R4**: A run can be cancelled through a tenant-scoped API. Cancellation is
  idempotent and cannot overwrite a succeeded, refused, or published terminal run.

### Grounded workflow and model gateway

- **M4-R5**: A fixed graph executes the sequence
  `load -> authorize -> retrieve -> generate -> validate -> draft -> risk ->
  approval-or-publish -> finalize`. Each node has a typed JSON-compatible state
  boundary and emits a public run event without raw prompt, document text, tool
  arguments, signed URLs, or raw model output.
- **M4-R6**: LangGraph state is persisted by the official PostgreSQL checkpointer
  with a stable `thread_id`. A Worker crash or lease reclaim resumes from the latest
  checkpoint. Business `AgentRun` state remains authoritative and a checkpoint cannot
  authorize a transition rejected by the business row.
- **M4-R7**: A `ChatModelGateway` protocol supports a deterministic local provider for
  reproducible tests and an OpenAI-compatible JSON provider with bounded timeout,
  retry classification, model/prompt version recording, and strict Pydantic output
  validation. Invalid model output is rejected or repaired by a bounded deterministic
  path; it cannot invent authorized citations or write capabilities.
- **M4-R8**: Retrieval calls the existing tenant/version-scoped Hybrid RAG service.
  The answer envelope contains only citations resolved by the existing citation
  validator. Empty/insufficient/low-relevance evidence produces a refusal and no
  write tool, approval, or downloadable artifact.

### Events, tools, and approval

- **M4-R9**: `AgentRunEvent` has a versioned envelope, strictly increasing per-run
  `seq`, unique `(run_id, seq)`, and tenant ownership.
  `GET /api/agent-runs/{run_id}/events` is an SSE stream with `Last-Event-ID` replay;
  reconnect returns only events after the cursor, with no duplicate or reordering.
- **M4-R10**: An MCP stdio server exposes exactly five documented tools:
  `search_document`, `read_chunk`, `create_draft_artifact`, `get_artifact`, and
  `publish_artifact`. The server validates principal context, tenant, resource,
  capability, strict input schema, timeout, idempotency key, and approval before any
  side effect. Model-provided tenant, actor, approval, or target fields are never
  trusted over the signed server context.
- **M4-R11**: Publication is an external-write capability. Without an unexpired,
  unconsumed approval bound to the exact operation, artifact, document version, and
  content fingerprint, `publish_artifact` deterministically denies and produces zero
  published side effects. Duplicate publish delivery is idempotent.
- **M4-R12**: Approval pause/resume is durable. Creating an approval request, emitting
  its event, approving/rejecting/expiring it, and creating a resume job are
  tenant-scoped and idempotent. Resume uses the same LangGraph thread and a validated
  `Command(resume=...)`; a stale target version or changed authorization denies resume.

### Artifact and delivery

- **M4-R13**: Draft and published artifacts have PostgreSQL metadata, deterministic
  object keys, SHA-256/size/content-type verification, and explicit visibility state.
  An artifact is downloadable only after object metadata and database state agree.
  Failed or cancelled runs do not expose an artifact, even if an orphaned object is
  later found by cleanup.
- **M4-R14**: The Web app provides a typed run workspace: choose a ready document
  version, submit a task, show replayable event history, render refusal/approval
  states, approve as an owner, cancel, and download a verified artifact. It uses
  fetch-based SSE parsing so the bearer token is not placed in a URL.

### Evidence and safety

- **M4-R15**: Tests cover graph/checkpoint recovery, model schema failures, citation
  authorization, tenant leakage, direct and retrieved prompt injection, MCP strict
  schemas, unapproved publication, approval races, SSE replay, artifact visibility,
  cancellation races, and duplicate delivery. Write-attempt injection tests assert
  zero effective publish side effects in the database and object store.
- **M4-R16**: A versioned deterministic safety/evaluation command reports grounded
  answer/refusal, citation authorization, approval, tool-policy, and SSE contract
  results from real service paths where infrastructure is required. A machine-
  readable M4 evidence manifest records exact commands, environment, commit, artifact
  hashes, limitations, and owner.

## Acceptance Criteria

- [x] API create/status/cancel/approval/artifact contracts are typed, tenant-scoped,
  idempotent, and return quickly; API tests prove no model or tool work runs inline.
- [x] AgentRun, AgentRunEvent, AgentRunExecution, AgentRunEvidence, ApprovalRequest,
  ToolExecution, and AgentArtifact persistence is covered by an additive Alembic
  migration with tenant ownership, unique/idempotency constraints, status checks, and
  downgrade tests.
- [x] The fixed graph runs with the deterministic local gateway and PostgreSQL
  checkpointer; crash injection at load, retrieve, generate, draft, approval, and
  publish boundaries resumes from persisted state without duplicate effective writes.
- [x] OpenAI-compatible gateway tests cover timeout, HTTP 4xx/5xx classification,
  malformed JSON, schema mismatch, bounded repair, model/version recording, and
  secret-free logs. No live provider key is required for the deterministic gate.
- [x] Real PostgreSQL retrieval integration proves tenant/version/generation filters
  are applied before answer generation and every returned citation resolves through
  the M3 validator.
- [x] SSE integration proves monotonic sequence allocation, reconnect using
  `Last-Event-ID`, replay after API restart, no duplicates, bounded heartbeats, and
  redaction of prompt/document/tool/model payloads.
- [x] MCP contract and integration tests prove all five tools reject missing or forged
  context, cross-tenant resources, extra schema fields, expired timeouts, missing
  idempotency keys, and unapproved publication; approved exact-version publication
  creates one visible artifact.
- [x] Approval tests cover approve/reject/expiry/revoke/cancel races, duplicate
  decisions, target-version replacement, authorization revocation, and resume after
  Worker crash. A run-level blanket approval is impossible.
- [x] Prompt-injection tests cover user text, retrieved document text, and MCP result
  text attempting to call publish, reveal secrets, or change tenant. All write
  attempts have zero effective publish/tool side effects.
- [x] Playwright covers upload-ready document selection, run submission, reconnectable
  event timeline, approval and artifact download, plus unauthorized and injection
  negative paths.
- [x] Existing M0-M3 quality, integration, evidence, and archived artifacts remain
  green and unchanged. M4 evidence is additive and explicitly labels deterministic
  provider results as local contract evidence, not production model quality.
- [x] `README.md` documents dependency installation, checkpointer setup, API/Web/
  Worker/MCP local commands, SSE/auth behavior, test/evidence commands, and known
  production gaps.

## Notes

- M2 Job remains the durable execution segment and lease/fencing source of truth.
  An AgentRun may have multiple `AgentRunExecution` rows: an initial segment pauses
  cleanly at approval, then an approval decision creates an idempotent resume segment.
  This avoids holding a Worker lease while a human is away.
- Tenant-wide membership is the current authorization boundary; per-document ACL/ABAC
  is a later production enhancement and must not be claimed as implemented here.
- M4 does not claim real model relevance, production QPS, public MCP exposure,
  Kubernetes/CD, or vLLM/model routing. Those belong to M5-M7 and external gates.
- Applied migrations and M0-M3 evidence are immutable. All new database changes use
  an additive revision and an explicit rollback test.
