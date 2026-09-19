# Core Backend Context

Use the factual project guides in `.trellis/spec/backend/`. Core owns reusable
settings, request context, health, database, logging, telemetry, durable Jobs, document
retrieval, Agent graph/gateway/checkpoint, approval, tool policy, and artifact contracts
under `packages/core`; it does not own application entry points. See
`.trellis/spec/backend/agent-mcp-hitl.md` for M4 facts,
`.trellis/spec/backend/observability-eval-load.md` for M5 facts, and
`.trellis/spec/backend/model-routing.md` for M7 facts.

Presales response sheets, bounded generation, review and CSV contracts are in
`.trellis/spec/backend/presales-workspace.md` (local workflow, commercial acceptance pending).

One-time tenant admission, atomic provisioning, explicit identity reuse and the
local operator CLI are in `.trellis/spec/backend/tenant-admission.md`. Real browser
authentication and commercial entitlement enforcement remain separate boundaries.

Durable browser identity sessions, login replacement, enterprise selection and
per-event authorization follow `.trellis/spec/backend/browser-sessions.md`.

Commercial period configuration, tenant locks, lifecycle gating, usage accounting,
authoritative storage/seat summaries and local operator recovery follow
`.trellis/spec/backend/entitlements-usage.md`.
