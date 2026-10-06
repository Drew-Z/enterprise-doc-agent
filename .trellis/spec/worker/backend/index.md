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

`WORKER__PRESALES_CONCURRENCY` bounds active background claim loops per Worker
process (1–4, default 1); it is distinct from synchronous tenant admission and is
not a cluster-wide quota. The single-node release profile remains one replica.
Each lane claims only when ready and registers its own `WorkerProgress` deadline;
an idle lane must never refresh a stuck lane's progress. Stop/cancellation or a
polling error cancels and joins all lanes before the shared embedding pool closes.
Only lane 0 selects demo jobs; other lanes exclude `DemoWorkspace` in the candidate
SQL before its limit. Preserve the existing global demo execution guard, durable
leases, per-operation dispatch slots, circuit breaker, and daily dispatch lock.
The PostgreSQL pool integration tests exercise overlap, queued overflow, exactly
one settlement, demo serialization, cancellation, polling failure and stale health.

The staging renderer accepts `--worker-presales-concurrency` and removes stale
overrides when unset. A schema 0032 `presales_concurrency` release explicitly binds
this sole configuration change plus approved images; all other settings, secrets,
resource limits and replicas are retained. Full rollback restores an absent key
as well as an existing value. Candidate tests do not establish live latency gains.
