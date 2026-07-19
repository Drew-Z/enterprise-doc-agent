# Core Backend Context

Use the factual project guides in `.trellis/spec/backend/`. Core owns reusable
settings, request context, health, database, logging, telemetry, durable Jobs, document
retrieval, Agent graph/gateway/checkpoint, approval, tool policy, and artifact contracts
under `packages/core`; it does not own application entry points. See
`.trellis/spec/backend/agent-mcp-hitl.md` for M4 facts,
`.trellis/spec/backend/observability-eval-load.md` for M5 facts, and
`.trellis/spec/backend/model-routing.md` for M7 facts.
