# M4 Agent MCP HITL: TDD Implementation Plan

## Preconditions

- M1, M2, and M3 are archived under `.trellis/tasks/archive/2026-07/`.
- The base branch is `feat/m3-document-rag`; the implementation branch is
  `feat/m4-agent-mcp-hitl`.
- M2 Job/Attempt/Event/Outbox and M3 DocumentVersion/retrieval/citation contracts are
  dependencies, not rewrite targets.
- `prd.md`, `design.md`, research files, `implement.jsonl`, and `check.jsonl` must
  validate before `task.py start`.
- No result is labeled as real model quality, production throughput, public MCP, or
  deployment evidence unless an executable external gate exists.

## TDD Rules

For every slice:

1. Add one focused failing test or executable contract and confirm the intended failure.
2. Implement the smallest coherent behavior that makes the focused test pass.
3. Run the focused test plus all affected package gates.
4. Refactor only while green and preserve tenant/error/logging contracts.
5. Commit at a rollback point before widening the next slice.
6. Never use skip, xfail, allow-failure, or repeated reruns to hide a gate.

## Activation

```powershell
uv run python .trellis/scripts/task.py validate 07-18-m4-agent-mcp-hitl
uv run python .trellis/scripts/task.py start 07-18-m4-agent-mcp-hitl
git branch --show-current
```

Expected branch: `feat/m4-agent-mcp-hitl`.

## Slice 0: Locked Dependencies And Package Boundaries

- [x] Add `apps/mcp` as a separately runnable workspace package and root dependency.
- [x] Lock `langgraph>=1.2.9,<2`, `langgraph-checkpoint-postgres>=3.1,<4`, and
  `mcp>=1.28.1,<2`; retain the exact resolved versions in `uv.lock`.
- [x] Add typed Agent/model/MCP settings. Local/test may use the deterministic gateway;
  non-local environments reject missing model/MCP secrets and deterministic provider.
- [x] Add an explicit `enterprise-doc-checkpointer-setup` command that calls the
  official PostgreSQL saver setup and a read-only `--check` mode.
- [x] Extend root quality/typecheck/test ownership to `apps/mcp` without weakening
  existing gates.

Focused commands:

```powershell
uv sync --frozen
uv run python scripts/setup_langgraph_checkpoints.py --help
uv run pytest tests/agent/test_dependency_contract.py -q
uv run mypy apps/mcp/src packages/core/src/enterprise_doc_core/agents
```

Rollback point: package imports and lockfile are green before any business schema exists.

## Slice 1: Agent Persistence And Migration

- [x] Write model metadata tests for AgentRun, AgentRunExecution, AgentRunEvent,
  AgentRunEvidence, ApprovalRequest, ToolExecution, and AgentArtifact.
- [x] Add additive Alembic revision `20260718_0009_agent_mcp_hitl.py` with named tenant,
  status, sequence, idempotency, approval-target, and artifact constraints.
- [x] Add migration integration tests for upgrade, indexes/constraints, representative
  valid/invalid rows, and downgrade after cleanup.
- [x] Add pure transition tests for run, approval, tool, and artifact status machines.

Focused commands:

```powershell
uv run pytest packages/core/tests/test_agent_models.py -q
uv run pytest tests/agent/test_m4_migration.py -m integration -q
uv run alembic upgrade head
uv run alembic current
```

Rollback point: schema and transition contracts only; no API or Worker behavior.

## Slice 2: Run Service, Events, And Fast API Creation

- [x] Implement canonical run fingerprinting and one-transaction AgentRun + initial
  AgentRunEvent + M2 Job/Outbox + AgentRunExecution creation.
- [x] Implement tenant-scoped status, execution/attempt projection, event listing,
  cancellation, ready-document-version listing, and stable public result models.
- [x] Allocate event sequence numbers under an AgentRun row lock and allow only
  event-type-specific redacted public payload schemas.
- [x] Add FastAPI routes for create/status/cancel/document-version listing with typed
  202/200/401/404/409/422 responses.
- [x] Prove API creation never calls the injected graph/model/tool adapters.

Focused commands:

```powershell
uv run pytest packages/core/tests/test_agent_service.py apps/api/tests/test_agent_api.py -q
uv run pytest tests/agent/test_agent_run_integration.py -m integration -q
```

Rollback point: runs can be created and inspected but the Worker has no Agent handler.

## Slice 3: Model Gateway And Grounded Answer Contract

- [ ] Define strict request/output schemas for QA, summary, and structured extraction.
- [ ] Implement DeterministicGroundedGateway using only authorized evidence and stable
  citation proposals.
- [ ] Implement OpenAICompatibleChatGateway with secret-safe HTTP handling, timeout,
  bounded response size, temperature 0, strict JSON parsing, and one syntactic repair.
- [ ] Classify timeout/429/5xx as retryable and auth/contract/schema failures as stable
  permanent errors. Never retry citation or authorization failures through the model.
- [ ] Reuse M3 `validate_citations()` and add tests for cross-tenant/version/candidate,
  excerpt alteration, empty evidence, and unsupported structured fields.

Focused commands:

```powershell
uv run pytest packages/core/tests/test_model_gateway.py -q
uv run pytest packages/core/tests/test_grounded_answer.py -q
```

Rollback point: validated model output exists as a Core contract without graph/tool writes.

## Slice 4: Artifact Store, Tool Policy, And MCP Stdio Server

- [ ] Add a bounded artifact object-store adapter for deterministic put, head, delete,
  and short-lived presigned GET without logging keys, filenames, bodies, or signatures.
- [ ] Implement signed per-run execution context with HMAC, expiry, nonce, capability,
  document version, and optional approval binding.
- [ ] Implement server-side policy that reloads membership, run, evidence, artifact,
  approval, and target version. Owner is required for publish.
- [ ] Implement idempotent ToolExecution begin/succeed/fail/deny transitions and stable
  error codes with input hashes only.
- [ ] Implement the five tools and the stable MCP v1 stdio server. stdout remains
  protocol-only; operational JSON logs use stderr.
- [ ] Add in-process/stdio protocol tests plus real PostgreSQL/MinIO integration for
  read, draft, exact-target publish, duplicate publish, and cross-tenant denial.

Focused commands:

```powershell
uv run pytest packages/core/tests/test_tool_policy.py apps/mcp/tests -q
uv run pytest tests/mcp -m integration -q
uv run enterprise-doc-mcp --help
```

Rollback point: MCP tools are independently usable and secure before graph integration.

## Slice 5: LangGraph, PostgreSQL Checkpoint, And Worker Segments

- [ ] Build the fixed graph and JSON-only state with stable graph/prompt/tool versions.
- [ ] Wire AsyncPostgresSaver through a dedicated lifecycle factory, require strict
  msgpack, and verify setup tables before Worker readiness becomes healthy.
- [ ] Implement an MCP stdio client adapter with injected context token, strict tool
  result parsing, timeout, cancellation, and process cleanup.
- [ ] Register `agent.execute` in the real Celery consumer and reuse M2 heartbeat,
  cancellation, lease, fencing, retry classification, and persistent event loop.
- [ ] Implement initial and resume execution payloads. A graph interrupt returns a
  successful segment outcome while AgentRun becomes `waiting_approval`.
- [ ] Add crash injection at every node boundary and prove checkpoint resume plus one
  effective retrieval freeze, model output, draft object, and terminal transition.

Focused commands:

```powershell
uv run python scripts/setup_langgraph_checkpoints.py --setup
uv run python scripts/setup_langgraph_checkpoints.py --check
uv run pytest packages/core/tests/test_agent_graph.py apps/worker/tests/test_agent_handler.py -q
uv run pytest tests/agent/test_graph_recovery_integration.py -m integration -q
```

Rollback point: non-publication runs and approval pause work through the real Worker.

## Slice 6: Approval Resume And Publication

- [ ] Implement idempotent approval request creation before the pure interrupt node.
- [ ] Add owner-only decision API with exact operation/resource/version/fingerprint,
  expiry/revoke/cancel checks, and stable decision idempotency.
- [ ] Create one resume Job/Outbox/AgentRunExecution in the approval transaction and
  resume with `Command(resume=...)` on the original graph thread.
- [ ] Implement publish tool consumption of approval and terminal AgentRun/artifact
  state with stable lock order and completion-race protection.
- [ ] Add approve/reject/expire/revoke/cancel and duplicate/concurrent decision tests,
  including authorization revoked or document version changed before resume.

Focused commands:

```powershell
uv run pytest packages/core/tests/test_approval_service.py apps/api/tests/test_approval_api.py -q
uv run pytest tests/agent/test_approval_resume_integration.py -m integration -q
```

Rollback point: full backend upload-ready-version to approved artifact path is complete.

## Slice 7: SSE Replay And Web Run Workspace

- [ ] Implement FastAPI StreamingResponse with tenant-scoped `Last-Event-ID`, ordered
  replay, disconnect handling, polling backoff, and comment heartbeats.
- [ ] Add SSE unit/integration tests for cursor boundaries, concurrent sequence writes,
  API restart replay, terminal close behavior, and sensitive-field rejection.
- [ ] Add strict Zod schemas and authenticated fetch-SSE parser in Web. Persist only
  run ID and last sequence, never token, prompt, citation text, or signed URL.
- [ ] Add an Agent workspace tab with ready-document selection, task controls, stable
  timeline, refusal, approval, cancel, reconnect, and artifact download states.
- [ ] Add responsive desktop/mobile tests and Playwright happy/reconnect/approval paths.

Focused commands:

```powershell
uv run pytest packages/core/tests/test_agent_sse.py apps/api/tests/test_agent_sse.py -q
uv run pytest tests/agent/test_agent_sse_integration.py -m integration -q
pnpm --filter web test
pnpm --filter web exec playwright test e2e/agent-workflow.spec.ts
```

Rollback point: local operator workflow is usable; no production deployment claim.

## Slice 8: Security, Evaluation, Documentation, And Evidence

- [ ] Add direct prompt, retrieved-document, and MCP-result injection corpora. Assert
  zero effective publish/object side effects and no secret/raw-body events or logs.
- [ ] Add unauthorized tenant/member/owner contract tests for run, SSE, citations,
  approval, tool, and artifact APIs.
- [ ] Add `evaluation/m4_agent_safety_v1.json` and `scripts/evaluate_m4_agent.py` that
  executes deterministic real service paths and reports grounded/refusal/citation,
  approval, tool-policy, replay, and side-effect results.
- [ ] Update README, factual Trellis specs, the code-backed interview document, and
  known limitations. Do not rewrite M0-M3 evidence.
- [ ] Commit reviewed implementation, run the complete evidence matrix once, save
  sanitized artifacts and SHA-256 values, add the immutable M4 manifest/index entry,
  validate Trellis, and commit evidence separately.

Focused commands:

```powershell
uv run pytest tests/security tests/contracts -q
uv run python scripts/evaluate_m4_agent.py
pnpm quality
uv run pytest -m integration -q
pnpm --filter web exec playwright test
uv run python .trellis/scripts/task.py validate 07-18-m4-agent-mcp-hitl
git diff --check
```

Rollback point: evidence commit is additive and independently reviewable.

## Full Completion Gate

Run from the repository root against the reviewed implementation commit:

```powershell
uv sync --frozen
pnpm install --frozen-lockfile
uv run ruff format --check .
uv run ruff check .
uv run mypy packages/core/src apps/api/src apps/worker/src apps/mcp/src
uv run pytest -m "not integration" -q
uv run pytest -m integration -q
pnpm lint
pnpm typecheck
pnpm test
pnpm build
pnpm --filter web exec playwright test
uv run python scripts/setup_langgraph_checkpoints.py --check
uv run python scripts/evaluate_m3_retrieval.py
uv run python scripts/evaluate_m4_agent.py
uv run python .trellis/scripts/task.py validate 07-18-m4-agent-mcp-hitl
```

## Completion Rules

- Do not archive until the upload-ready-document -> Agent -> approval -> artifact path
  has real PostgreSQL/MinIO/Redis/Worker evidence and Playwright coverage.
- A deterministic gateway proves orchestration and policy only. It is not real model
  quality or production answer evidence.
- An MCP unit test is not enough for publication: real server-side tenant/approval
  integration must prove zero side effects on denial and one side effect on replay.
- A streamed HTTP response is not enough for SSE: persisted sequence/replay after API
  restart and sensitive-field redaction must pass.
- Keep all external deployment/capacity/model-quality gaps explicit for M5-M7.
