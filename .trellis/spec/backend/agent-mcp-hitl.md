# Agent, MCP, And HITL Contracts

M4 uses a fixed LangGraph workflow rather than an unconstrained ReAct loop. Business
state remains in PostgreSQL; the official LangGraph PostgreSQL checkpointer resumes
node execution but cannot override tenant, run, approval, or artifact rows.

- API run creation writes AgentRun, initial event, Job, Outbox, and execution only.
- Worker execution uses the M2 lease, heartbeat, cancellation, and fencing contract.
- Grounded output must cite the frozen authorized M3 evidence set and pass deterministic
  citation validation before draft creation.
- Prompt behavior `m4.v3` requires complete explicit requested facts, the minimum sufficient
  citation set, identifiers and document versions copied from the same supplied evidence item,
  and contiguous verbatim excerpts. Prompt behavior `m4.v4` inherits those rules and also
  requires answers to stand alone with every material qualifier from controlling evidence while
  treating conflicting user text as untrusted rather than repeating it as policy. Prompt behavior
  `m4.v5` requires a complete controlling evidence sentence for direct single-sentence answers,
  forbids echoing conflicting values even while correcting them, and requires the shortest
  sufficient citation span. For known supplied chunk/version pairs only, it permits one bounded
  citation-only repair of non-verbatim excerpts; answer content, citation identifiers, order, and
  already-valid excerpts remain immutable. Prompt behavior `m4.v6` inherits the v5 rules and makes
  the sentence boundary explicit: when one complete evidence sentence fully supports the answer,
  the citation excerpt starts at that sentence and stops at its boundary rather than extending into
  adjacent sentences in the same evidence item. Prompt behavior `m4.v7` additionally forbids
  duplicate citation pairs, requires one minimum sufficient span when one evidence item supports
  multiple requested facts, and forbids repeating conflicting instructions, actions, commands,
  claims, or values from untrusted input. The gateway selects rules from the persisted prompt
  version, so older runs retain their prior prompt and repair behavior.
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

## Scenario: Durable Secret-Safe Attempt Diagnostics

### 1. Scope / Trigger

- Trigger: grounding and MCP failures need a stable subreason for staging diagnosis without
  exposing exception messages, document text, tool payloads, runtime IDs, URLs, or credentials.
- Ownership: Core defines and persists allowlisted codes; Worker classifies typed failures; the
  authenticated Job and Agent status APIs project the stored value.

### 2. Signatures

- Database: `job_attempts.diagnostic_code VARCHAR(100) NULL`.
- Runtime: `JobRuntimeService.fail(..., diagnostic_code: str | None = None)`.
- Worker: `JobHandlerError(..., diagnostic_code: str | None = None)`.
- API: `attemptHistory[].diagnosticCode: string | null` on authenticated Job and Agent status.

### 3. Contracts

- Grounding codes are exact members of `GROUNDING_DIAGNOSTIC_CODES`.
- MCP codes have the exact shape `mcp.<known_tool>.<allowlisted_subcode>`.
- Unknown, malformed, or oversized values persist as null. MCP tool errors with no recognized
  stable code use `mcp.<known_tool>.returned_error`.
- Public `errorCode` and retryability remain independent from `diagnosticCode`.
- Diagnostics never enter Agent events, Prometheus labels, or report fields without a second
  allowlist check.

### 4. Validation & Error Matrix

- Typed `GroundingValidationError` with an allowlisted diagnostic -> preserve public code and
  diagnostic.
- Arbitrary exception with a `diagnostic_code` attribute -> preserve existing public
  classification, discard the diagnostic.
- MCP structured `code` / `errorCode` / `error_code` with an allowlisted exact value -> bind it to
  the requested known tool.
- MCP wrapper text `Error executing tool <requested_tool>: <allowlisted_code>` -> accept the exact
  suffix only.
- Unknown payload, message text that merely mentions a code, or a different tool operation ->
  `mcp.<known_tool>.returned_error`.
- Direct runtime caller supplies a non-allowlisted code -> persist null.

### 5. Good/Base/Bad Cases

- Good: `grounding.citation_excerpt_not_verbatim` survives queue settlement and appears in the
  authenticated attempt history.
- Base: old attempts and successful attempts return `diagnosticCode: null`.
- Bad: raw provider text such as `token=... tool_input_invalid ...` is never promoted or stored.

### 6. Tests Required

- Core unit tests assert every grounding public-code/diagnostic pair and allowlist rejection.
- Worker boundary tests assert exact MCP text/structured parsing, wrong-operation rejection,
  retryability stability, and typed-only grounding propagation.
- Database integration tests assert nullable migration round-trip and durable attempt persistence.
- API tests assert camelCase projection and absence of `error_message` from attempt responses.
- Staging evaluator tests seed secret-like values and verify only allowlisted diagnostics survive
  into a seal-valid report.

### 7. Wrong vs Correct

#### Wrong

```python
diagnostic_code = str(error)
```

#### Correct

```python
diagnostic_code = (
    error.diagnostic_code if isinstance(error, GroundingValidationError) else None
)
```
