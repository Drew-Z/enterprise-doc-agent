# Agent, MCP, And HITL Contracts

M4 uses a fixed LangGraph workflow rather than an unconstrained ReAct loop. Business
state remains in PostgreSQL; the official LangGraph PostgreSQL checkpointer resumes
node execution but cannot override tenant, run, approval, or artifact rows.

- API run creation writes AgentRun, initial event, Job, Outbox, and execution only.
- Worker execution uses the M2 lease, heartbeat, cancellation, and fencing contract.
- Grounded output must cite the frozen authorized M3 evidence set and pass deterministic
  citation validation before draft creation.
- MCP exposes exactly five strict stdio tools. Signed execution context, membership,
  capability, current execution, document version, artifact, and approval are reloaded
  server-side before effects.
- A cancelled `search_document` marks its execution immediately retryable. `started_at` is the
  retrieval lease version: stale takeover advances it, and evidence freeze or terminal writes
  must fence on the expected value so an older retrieval cannot overwrite the current attempt.
- Worker MCP error handling searches both text and structured protocol error payloads for the
  allowlisted retryable codes; payload container shape must not change retry classification.
- Publication requires an active owner and one unexpired exact-target approval bound to
  operation, artifact, document version, and fingerprint. Replay is idempotent.
- Agent events contain allowlisted public payloads only. Prompt, document, model/tool
  bodies, execution-context tokens, object keys, and signed URLs are not event fields.

## Proven Examples

- `packages/core/src/enterprise_doc_core/agents/`
- `apps/worker/src/enterprise_doc_worker/agent_backend.py`
- `apps/mcp/src/enterprise_doc_mcp/server.py`
- `tests/agent/`, `tests/mcp/`, `tests/security/`, and `tests/contracts/`
