# Web Frontend Context

Use the factual project guides in `.trellis/spec/frontend/`. Web owns React UI and
the typed health/upload/Agent clients under `apps/web`, and communicates only with the
API except for short-lived direct object-store transfers. See
`.trellis/spec/frontend/agent-workspace.md`.

The presales response/review/export route is documented in
`.trellis/spec/frontend/presales-workspace.md`.

Browser login/selection/admission/logout, captured credentials, cross-tab changes
and all authenticated transports follow `.trellis/spec/frontend/browser-sessions.md`.

Owner generation capacity, storage/seats, nullable costs and query isolation follow
`.trellis/spec/frontend/tenant-usage.md`.
