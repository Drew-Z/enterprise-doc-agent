# Agent Run Workspace

The Web Agent workspace uses strict Zod response schemas and authenticated fetch-based
SSE. It selects ready document versions, creates QA/summary/extraction runs, renders an
ordered timeline, reconnects with `Last-Event-ID`, handles cancellation and owner
approval, and downloads only freshly verified artifacts.

Session storage owns the local API token. Local storage may contain only the versioned
run ID and last sequence. Prompt text, citations, bearer tokens, approval fingerprints,
object keys, and signed URLs are not recovery data. Event history is paged in 500-item
batches and resumes from the persisted cursor.

## Proven Examples

- `apps/web/src/agent/`
- `apps/web/e2e/agent-workflow.spec.ts`
- `apps/web/src/agent/AgentWorkspace.test.tsx`
