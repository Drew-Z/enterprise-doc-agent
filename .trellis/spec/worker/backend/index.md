# Worker Backend Context

Use the factual project guides in `.trellis/spec/backend/`, especially directory
structure, health error handling, logging, and quality. Worker lifecycle and probes
remain under `apps/worker`; shared infrastructure remains in `packages/core`.
The Worker owns `agent.execute` graph segments, PostgreSQL checkpoint lifecycle, and
the authenticated MCP stdio client while reusing M2 lease/fencing semantics.
