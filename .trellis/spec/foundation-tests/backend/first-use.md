# Integrated browser first-use acceptance

## 1. Scope / Trigger

Use this contract when validating the connection between browser admission,
invitations, ingestion, Presales, usage and session retirement. This suite is a
single stateful journey with named steps. Separate historical browser and database
results cannot substitute for running it. Accounts and documents are synthetic.
The default suite uses a signed test IdP; the separate Keycloak suite runs a real
local provider. Both use controlled models and local mail capture, so public
identity hosting/delivery, customer quality and payment acceptance remain separate.

## 2. Signatures

- `tests.first_use.browser_server` starts the production `create_app(settings=...)`
  on 127.0.0.1:18769 and the signed test IdP/model controls on localhost:18770.
- `FirstUseHarness.start()` creates all current metadata in the unique
  `invitation_e2e_<runId>` schema. It issues admission grants but does not seed
  Tenant/User/Membership/Binding, ready versions, chunks or usage events.
- `UploadedEvidenceModel.respond(request)` is shared with the older ingestion
  harness. S5 reaches it through real loopback HTTP; the older suite still uses
  MockTransport. Citations must come from actual retrieved uploaded evidence.
- `apps/web/playwright.first-use.config.ts` requires a fresh absolute existing
  `FIRST_USE_OUTPUT_DIR`. It reuses `vite.invitation.config.ts` on 127.0.0.1:5173.
- `firstUseConfig("signed" | "keycloak")` selects explicit project metadata and
  the server. `playwright.keycloak-first-use.config.ts` starts
  `tests.first_use.keycloak_server`, which calls the same production API factory
  with `FirstUseHarness(output, keycloak_auth=BrowserAuthSettings(...))`.
- `KeycloakFixture.start()/snapshot()/close()` own one pinned 26.7.0 Compose
  project. `browser_context(account)` and `mail_link(account, offset)` serve
  in-memory credentials/action links only through authenticated test controls.
- Test controls require `X-Invitation-Run`: `/test/context`, `/test/files/{name}`,
  `/test/configure`, `/test/publisher/{pause|resume}`, `/test/model/fail-next`,
  `/test/state`, `/test/shutdown`. They are on the separate test IdP, not the API.

## 3. Contracts

Do not inject a principal resolver, PresalesService or UsageService into the API.
The default factory must connect the actual usage service to generation so valid
draft persistence consumes the reserved request. Period configuration calls the
real EntitlementAdministrationService for tenants accepted from this run's grants.
Synthetic limits (4 and 1 requests, 32 MiB and 2 seats) are test values, not pricing.

Use the browser controls for successful admission, invitation, upload, creation,
generation, review and CSV actions. Auxiliary same-origin cookie requests verify
negative access and persisted state. Do not enter bearer tokens or document IDs
in the UI. Observe each upload's hash Worker and verify original/object SHA and
the Worker verification marker; multipart negotiation may start multiple Workers.

Publisher pause/resume controls actual tenant-scoped Outbox stores. Redis must
start empty under the unique `first-use-<runId>:` namespace. The real solo Celery
worker parses and embeds files with HashEmbeddingProvider. Both model and embedding
configuration are explicit; this suite makes no external model requests.

Switch races delay a response obtained by `route.fetch()`, not fabricated content.
The original fetch must be aborted and neither browser tab may show the old
enterprise's usage. Mobile switching first opens the navigation drawer. On an
unchanged usage hash, click Refresh usage instead of assuming navigation refetches.

Cleanup stops HTTP, publisher and worker before capturing final database evidence.
Check each upload's admitted tenant and `build_object_key` before removing that
exact key/multipart ID; delete only enumerated keys under the initially empty
Redis prefix. Drop only the created schema. Compare read-only repeatable-read
public-table counts/SHA before and after; any difference fails the run. This is
current-metadata integration acceptance, not an Alembic migration test.

Trace is off. Clear password drafts and copied invitation clipboard text. Never
persist admission/invitation/cookie/CSRF/context credentials, JWTs or signed URLs.
Network evidence records paths/status/header-presence booleans only. Preserve raw
results and failure diagnostics after a secret scan; do not relabel failures.

### Real Keycloak evidence

Keycloak runs at `http://localhost:<random-port>` and the product at
`http://127.0.0.1:5173`. Separate ports on the same host do not isolate host-only
cookies. Observe actual IdP request headers and require zero product cookies.
The real control service has no synthetic authorize/token/JWKS endpoints.
Admission grants and persisted bindings must use the real issuer.

Two users start with `emailVerified=false`. The browser follows real SMTP messages
from the private HTTP capture. Before verification, `/auth/session` returns
`200 {"status":"anonymous"}`; business endpoints remain unauthorized. Do not
change that session contract to satisfy a test expecting 401.

`identityProvider="keycloak"` snapshots omit the synthetic `protocol` counters.
Read client settings, users and CODE_TO_TOKEN/UPDATE_PASSWORD events from Keycloak,
then compare every database binding's issuer/subject with the actual users.
After both verifications and the password-reset/relogin extension, require
`verifiedUsers=2`, `successfulCodeExchanges=4`, `passwordUpdates=1`,
`capturedEmails=3`, `bindingCount=4`, `bindingsMatchKeycloak=true`, and two product
users. Require S256, confidential client, exact callback, implicit/password grants
disabled. Assert the original actor and both enterprise memberships survive reset.
Explicitly clear IdP cookies to force the reset login and use a fresh browser for
old-password rejection; product logout is not an IdP global-logout assertion.

For Keycloak, also disable video/screenshots on failure and set
`PLAYWRIGHT_NO_COPY_PROMPT=1` before execution: Playwright 1.61.1's automatic
`error-context.md` can otherwise contain live identity action URLs even with trace
off. Use explicit screenshots before filling passwords and sanitized stage errors.
Final teardown requires both `cleanup.json.success` and
`keycloak-cleanup.json.status="passed"`, including actual resource absence.

## 4. Validation & Error Matrix

| Trigger | Required result |
|---|---|
| Two browser admissions and two invitations | Two enterprises, two users, two active members per enterprise; pending invitations occupy no seat |
| Paused publisher | Three completed uploads, pending jobs/outbox, no generation; disabled source entry and 404 on attempted create |
| Valid TXT/PDF/DOCX | Ready versions, verified SHA, real succeeded jobs and published outbox, exact excerpt/heading/page |
| Other author or enterprise | 404 for private sheets/foreign sources; owner is not another author's reader |
| Exhausted generation limit | HTTP 429 before any new model request, attempt, reservation or event |
| Broken PDF | Failed/inactive generation, dead job, zero chunks, disabled source |
| Local model HTTP 503 | Failed attempt, one release, unknown cost; refresh does not retry |
| Explicit retry succeeds | Original failed attempt retained; one successful draft consumes one unit |
| Member usage / inactive membership / stale context | 403 / 403 / 409 from actual authentication and authorization |
| Logout | Both tabs close; subsequent business requests return 401; session cookies are removed |
| Unverified real identity / rejected old password | Session endpoint remains anonymous; no product session established |
| Real verification, reset and new-password login | Two verified users, four real exchanges, one password update, three captured emails; actor/bindings retained |
| Synthetic protocol counters in real-provider evidence | Reject the evidence; query Keycloak and persisted bindings instead |
| Cleanup or public-data comparison fails | Browser acceptance fails; retain exact receipt for recovery |

## 5. Good / Base / Bad Cases

- Good: the same owner and colleague complete three reviewed CSV exports across
  two enterprises, with five successful drafts and one released failure recorded.
- Base: a corrected upload becomes selectable; the failed file remains excluded.
- Good (Keycloak): the same business journey passes after real verification, and
  password reset returns to the same actor and enterprise relationships.
- Bad: mark jobs ready, seed memberships, disable the usage service or combine
  unrelated test results to claim this journey passed.

## 6. Tests Required

```powershell
$env:FIRST_USE_OUTPUT_DIR = '<fresh existing absolute output directory>'
pnpm.cmd --filter web exec playwright test -c playwright.first-use.config.ts
# Use a different fresh directory before running the real-provider variant.
pnpm.cmd --filter web exec playwright test -c playwright.keycloak-first-use.config.ts
& .\.venv\Scripts\python.exe -X utf8 -B -m pytest tests/first_use/test_keycloak_first_use_integration.py -q
& .\.venv\Scripts\python.exe -X utf8 -B -m pytest tests/identity_provider -q
```

Keep one browser worker and refuse occupied ports. Do not run browser-session,
invitation or ingestion suites concurrently: they share port 5173. A shared model
fixture change also requires `playwright.presales-ingestion.config.ts` with its
own fresh `PRESALES_INGESTION_OUTPUT_DIR`. Run backend/Web quality gates and the
session/invitation/billing/Presales integration regression. Inspect desktop and
390px screenshots, inspect CSV BOM/formula protection, scan retained evidence,
verify cleanup and protect pre-existing workspace hashes before local delivery.
The fixture integration must observe initially unverified users and real client
configuration; injecting Docker `down` failure after actual removal must still
produce a failed cleanup receipt. Preserve separate signed and Keycloak runs.

## 7. Wrong vs Correct

Wrong: one multipart completion proves ready ingestion, or a released request means
the provider charged nothing. Correct: verify real Worker jobs/chunks/full hashes,
and keep technical capacity separate from unknown provider cost.

Wrong: a passed local signed IdP and deterministic model prove a paid service is
ready. Correct: mark only integrated local first-use complete; retain the separate
real-service/customer/payment checklist and its unassigned owners.

Wrong: separate loopback ports isolate cookies, trace-off removes all identity
URLs, or the signed test IdP's counters prove real exchanges. Correct: use distinct
hosts, disable automatic failure snapshots, and read real Keycloak state/events
plus the product database. A local capture receipt never proves public sending.

## Proven Examples

- `tests/first_use/harness.py`
- `tests/first_use/browser_server.py`
- `tests/first_use/keycloak_fixture.py`
- `tests/first_use/keycloak_server.py`
- `tests/first_use/test_keycloak_first_use_integration.py`
- `tests/first_use/evidence.py`
- `tests/presales/model_fixture.py`
- `apps/web/first-use-e2e/journey.spec.ts`
- `apps/web/first-use-e2e/keycloak.ts`
- `apps/web/first-use-e2e/boundaries.ts`
- `apps/web/first-use-e2e/teardown.ts`
