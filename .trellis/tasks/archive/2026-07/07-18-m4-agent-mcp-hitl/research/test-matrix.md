# M4 Test And Evidence Matrix

## Unit Contracts

- AgentRun, approval, tool, artifact, and graph transition tables reject illegal states.
- Run fingerprints are deterministic; replay/conflict/tenant independence are explicit.
- Event sequence allocation and public payload allowlists reject raw prompt/document/
  model/tool/signed-URL fields.
- Model gateway tests cover deterministic output, timeout, 429/5xx, auth/4xx,
  malformed JSON, schema mismatch, output bounds, repair limit, and secret-free errors.
- Citation tests cover wrong tenant, version, candidate, excerpt, and evidence changes.
- Signed MCP context tests cover signature, expiry, nonce, capability, run, actor,
  tenant, document version, and approval tampering.
- Tool schemas reject extra fields and all policy dimensions fail closed.
- Checkpoint state rejects unknown graph/prompt/tool versions and non-JSON state.
- SSE parser tests cover partial frames, multi-line data, duplicate IDs, gaps, invalid
  cursors, heartbeat comments, cancellation, and reconnect backoff.

## PostgreSQL/MinIO/Redis Integration

- Concurrent create requests produce one AgentRun, initial event, Job, Outbox, and
  execution segment.
- Concurrent event writers produce a contiguous unique sequence.
- Graph recovery at every node boundary uses the same checkpoint thread and does not
  duplicate AgentRunEvidence, model result, artifact object, approval, or publication.
- Approval pause commits run/event/approval state; approve/reject/expire/cancel races
  create at most one resume job and one terminal result.
- MCP stdio calls revalidate real database ownership and membership. Cross-tenant and
  unapproved publish attempts produce zero ToolExecution success, zero published
  artifact, and zero visible object.
- Artifact object/database crash windows are replay-safe and visibility requires a
  matching `HEAD` size/hash.
- SSE replay after API restart returns only `seq > Last-Event-ID` in order.
- M2 fencing prevents a stale/cancelled segment from finalizing or publishing.

## Security Gates

Injection corpus categories:

1. Direct user request: ignore policy, use another tenant, publish without approval.
2. Retrieved document: fake system/admin instruction, secret request, tool call request.
3. MCP result: embedded text requesting a second privileged tool or target change.
4. Citation: foreign chunk/version, invented excerpt, candidate-set swap after approval.
5. Approval: blanket run approval, expired/revoked/consumed row, changed artifact hash.

Each write-capability case asserts effective database and object-store side-effect
counts, not only returned text or an exception class.

## API And Protocol Contracts

- OpenAPI snapshots cover run create/status/cancel/events, approval decision, ready
  document versions, artifact list, and download authorization.
- SSE response uses `text/event-stream`, versioned JSON, monotonic `id`, and a stable
  `agent-run` event name.
- MCP Inspector/client contract lists exactly five tools with strict schemas and no
  model-visible tenant, actor, token, object key, or approval authority.
- Error responses do not reveal whether a foreign run/artifact/approval exists.

## Web And Playwright

- Unit: Zod validation, reducer legality, cursor persistence, reconnect, refusal,
  approval, cancel, and artifact states.
- Happy path: ready document -> create run -> event replay -> approval -> resume ->
  verified artifact download.
- Reconnect: refresh after several events and during approval; timeline has no duplicate
  or reordering.
- Unauthorized: second tenant cannot see run/events/approval/artifact.
- Injection: malicious document text can be cited as evidence but cannot publish.
- Responsive: desktop and mobile controls/timeline do not overlap or resize on events.

## Evidence Outputs

Required artifacts under `evidence/m4/artifacts/`:

- dependency-lock and checkpointer-setup logs;
- backend/frontend quality logs;
- migration/checkpoint/graph/MCP/approval/SSE/artifact integration logs;
- security side-effect report;
- deterministic Agent/safety evaluation JSON;
- Playwright report and selected screenshots;
- sanitized local workflow report with versions and limitations.

The M4 manifest uses the parent evidence schema and SHA-256 for every evidence artifact.
It must say that deterministic model and controlled local documents are orchestration
evidence, not production answer quality or capacity.
