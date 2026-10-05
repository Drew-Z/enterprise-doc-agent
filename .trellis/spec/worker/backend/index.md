# Worker Backend Context

Use the factual project guides in `.trellis/spec/backend/`, especially directory
structure, health error handling, logging, and quality. Worker lifecycle and probes
remain under `apps/worker`; shared infrastructure remains in `packages/core`.
The Worker owns `agent.execute` graph segments, PostgreSQL checkpoint lifecycle, and
the authenticated MCP stdio client while reusing M2 lease/fencing semantics.

The presales loop owns its embedding HTTP pool through `managed_embedding_provider`.
Keep that context inside the running event loop and around the full loop; normal exit,
failure and cancellation must close the pool. Never replace it with a process-global
async client. Client reuse does not share tenant metering scopes or change per-request
credentials, retry limits or timeout. One-shot embedding callers retain their existing
ownership. The real loopback HTTP lifecycle test verifies keep-alive reuse and closure;
it does not establish production latency or supplier reliability.
