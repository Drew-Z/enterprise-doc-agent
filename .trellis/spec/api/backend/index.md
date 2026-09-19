# API Backend Context

Use the factual project guides in `.trellis/spec/backend/`, especially directory
structure, error handling, logging, and quality. API-owned code is under `apps/api`;
shared infrastructure remains in `packages/core`. M4 API ownership includes Agent run,
ordered event/SSE, exact approval, and verified artifact endpoints.

The presales API is documented in `.trellis/spec/backend/presales-workspace.md`.

Browser OIDC, cookie/context/CSRF authentication and admission HTTP are documented
in `.trellis/spec/backend/browser-sessions.md`.

Owner usage states/resources and Presales commercial 403/429/503 contracts are documented
in `.trellis/spec/backend/entitlements-usage.md`.
