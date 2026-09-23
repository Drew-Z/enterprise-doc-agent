# Foundation Test Context

Use the quality and cross-layer guides. Cross-package executable contracts live in
`tests/foundation`; integration tests are explicitly marked and use real local
infrastructure.
M4 cross-package contracts also live in `tests/agent`, `tests/mcp`, `tests/security`,
and `tests/contracts`; browser recovery and approval paths live in `apps/web/e2e`.

Task-local synthetic workflow experiments have a separate
[presales validation contract](./presales-validation.md). These diagnostic tools
do not replace production RAG quality gates or customer validation.

Real requests against the frozen synthetic input follow the separate
[model trial contract](./presales-model-trial.md), including transport failures,
immutable output evidence, unknown costs and independent review boundaries.

Deployed Presales API repeat runs use the [public-demo quality contract](./presales-quality.md):
fixed input/gold separation, real uploads, independent demo enterprises, no retries,
source-bound scoring and unknown billing amounts. This is separate from browser UX acceptance.

Real file uploads through local JWT authentication, MinIO, tenant-scoped Outbox
delivery and Redis/Celery follow the [presales ingestion contract](./presales-ingestion.md).
It records full-file SHA verification, browser source entry, failure recovery and
exact test-resource cleanup. Controlled responses are not external model trials.

Tenant admission uses the [admission verification contract](./tenant-admission.md)
for real PostgreSQL lock races, isolated migration dependencies, durable replay,
Windows credential ACLs and exact test-schema cleanup.

The [browser session contract](./browser-sessions.md) covers signed local HTTP IdP,
real database authorization, Chromium, additive migration and exact object cleanup.

The [entitlement contract](../../backend/entitlements-usage.md) covers real PostgreSQL
configuration/reservation races, audit atomicity, period boundaries, CLI recovery
after lost acknowledgement, and Presales/API behavior. These tests use fixture-owned
UUID cleanup and controlled models; they do not prove payment or provider acceptance.

Resource summary tests in `tests/billing/test_tenant_resource_usage_integration.py`
add real PostgreSQL storage/seat counts in all period states and real-service ASGI
owner/member isolation. The separate Web usage browser suite mocks HTTP and must
not be combined with these results as a complete first-use journey.

The [integrated first-use contract](./first-use.md) connects signed local OIDC or
a separately evidenced real local Keycloak provider,
browser admission/invitations, real ingestion, Presales review/CSV, ledger/resource
usage and session isolation through the unmodified production API factory. Its
controlled model and synthetic accounts remain distinct from live acceptance.
The Keycloak variant adds real email verification, password reset, old-password
rejection, persisted issuer/subject checks and dual product/identity cleanup.

`tests/identity_provider` follows the [identity/mail contract](../../infrastructure/backend/identity-mail.md).
It adds real Keycloak verification/reset and existing OIDC-client checks with a
local mail capture boundary; it does not relabel S5 as a real-provider journey.

`tests/cloudflare_mail` follows the [private mailbox package contract](../../infrastructure/backend/cloudflare-mail.md).
It checks source/directory protection, private HTTP/email handlers and real local
workerd/D1/Chromium isolation using synthetic messages; no public mail is sent.

The [mailbox rollout contract](../../infrastructure/backend/cloudflare-mail-rollout.md)
adds HTTP-boundary provisioning/recovery tests and real public HTTPS/API/Chromium
acceptance. Public empty inbox access remains separate from Routing, Sending and
the full product identity journey; no mail is sent by the public harness.
