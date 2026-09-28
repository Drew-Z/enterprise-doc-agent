# Agent Run Workspace

The Web Agent workspace uses strict Zod response schemas and authenticated fetch-based
SSE. It selects ready document versions, creates QA/summary/extraction runs, renders an
ordered timeline, reconnects with `Last-Event-ID`, handles cancellation and owner
approval, and downloads only freshly verified artifacts.

Session storage owns the local API token. Local storage may contain only the versioned
run ID and last sequence. Prompt text, citations, bearer tokens, approval fingerprints,
object keys, and signed URLs are not recovery data. Event history is paged in 500-item
batches and resumes from the persisted cursor.

## Persisted execution history

`GET /api/agent-runs/{id}` returns `executions[].attemptHistory[].diagnosticCode`
as a nullable string, including null on successful attempts. The strict Web
`agentRunAttemptSchema` accepts this declared field and permits omission by older
servers. Other unknown fields and non-string/non-null diagnostics remain invalid.
Initial execution sequence is zero. Test `AgentApiClient.getRun` at the HTTP
boundary with nonempty execution and attempt arrays; empty fixtures cannot detect
this contract drift. Rejecting the added field previously hid completed tasks and
their artifacts behind a loading status and protocol error.

The browser acceptance check must reach status, event history, answer preview,
citations, signed download and refresh. An API-only status/download check does not
prove that the strict Web parser can render the same task. A CDP check may use an
explicit staging authentication adapter, but business responses must remain real
and the result must identify whether static assets are deployed or locally built.

## Proven Examples

- `apps/web/src/agent/`
- `apps/web/e2e/agent-workflow.spec.ts`
- `apps/web/src/agent/AgentWorkspace.test.tsx`
- `apps/web/src/agent/api/client.test.ts`: nullable/legacy diagnostics, initial zero
  sequence, malformed diagnostics and undeclared-field rejection through getRun.
