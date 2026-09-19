# Browser Session Verification Boundary

## Scope / Trigger

Use when validating browser identity, the additive session migration, the local
OIDC/Chromium harness or evidence/resource cleanup. A local test issuer is not a
real customer identity provider, and synthetic accounts are not trial customers.

## Signatures

- `tests.browser_sessions.browser_server`: loopback HTTP test IdP on 18768 and
  actual API on 18767, with a unique `browser_e2e_` PostgreSQL schema.
- `BROWSER_SESSION_OUTPUT_DIR`: an existing dedicated output directory.
- `apps/web/playwright.browser-session.config.ts`: Chromium, one worker, retries 0,
  tracing off, strict Web port 5173 and no reused servers.
- `create_previous`, `migrate_browser`, `browser_db`: isolated schema fixtures.

## Contracts

Validate every infrastructure endpoint as loopback and preserve the validated
local/test environment. Use real RSA signing/JWKS, one-use code and PKCE verifier
exchange over HTTP. API OIDC verification, session services, admission and principal
joins are not replaced. Hostnames differ for IdP (`localhost`) and Web (`127.0.0.1`)
so host-only application cookies cannot leak to the test IdP by sharing a hostname.

The harness creates two bound enterprises, an unbound identity with the same email,
and a new identity with an actual admission grant. Its test-only context/expiry/
shutdown routes exist only on the separate test IdP and require a test header.
Secrets remain in memory; output contains only identifiers, booleans, hashes and
protocol counts. Do not expose a bypass route in the production application.

The browser suite exercises a real File/hash worker/multipart HTTP/MinIO upload to
completion. It has no Outbox publisher, Worker, parser, embedding or model process.
Therefore its completed upload can remain processing. The separate ingestion
suite owns the full upload-to-ready/presales pipeline evidence.

Cleanup verifies the isolated schema, resolves exact object keys from upload-session
and pending-version UUIDs and checks the tenant belongs to that schema. Object keys
do not contain tenant IDs. Abort only the recorded exact multipart ID, delete only
the exact key, then verify zero matching objects and multipart uploads before
dropping the owned schema. Do not sweep shared buckets or Redis. Servers stop
before cleanup; resource receipts and process/port checks are required.

Old security suites run with only their engine search_path redirected to an owned
schema, not modified principal behavior. Copy their task-local runner before reuse
when it writes a receipt beside itself: never overwrite a previous delivery record.
Public migration verification hashes all pre-existing table rows under a read-only
repeatable-read snapshot; only the three browser tables and Alembic version are added.

## Validation & Error Matrix

| Observation | Evidence requirement |
|---|---|
| Startup configuration failure | Preserve failure; do not imply browser cases ran |
| Browser assertion failure | Keep first result and safe diagnostics before correction |
| Aborted initiation lacks Cookie | Prove ERR_ABORTED and no response; do not call it a sent authenticated request |
| Upload complete | Hash/HTTP/object evidence; no claim that processing is ready |
| Teardown failure | Keep exact resources and failed receipt; do not hide failure |
| Old public data differs | Stop migration acceptance and identify changed tables without exposing rows |
| Cleanup tool rejection | Record exact action/reason; do not use another tool to bypass it |

## Good / Base / Bad

- Good: real protocol and database services, safe presence-only network evidence,
  private schema, exact object cleanup and unchanged public-data hashes.
- Base: unit HTTP may use MockTransport; it is labeled separately from HTTP IdP tests.
- Bad: seed a browser bearer, replace the resolver, leak failure-snapshot input
  values, reset public or claim synthetic accounts prove customer readiness.

## Tests Required

Run browser-session PostgreSQL/HTTP/race tests, OIDC verification, bearer SSE and
legacy membership/binding/ACL/logout/SCIM regression. Run Python quality gates and
Web lint/typecheck/test/build. Validate Vite's actual same-origin forwarding and
Nginx syntax. Inspect desktop/narrow-screen screenshots, scan evidence for secrets,
verify old dirty-file hashes and retain unknown/pre-existing resources.

## Wrong vs Correct

Wrong: `create_all(checkfirst=True)` with `search_path=private,public` and an
assumption that dependency tables will be created privately.

Correct: create the known prior dependency schema with `checkfirst=False`, assert
the actual current schema/table set, and apply the real browser migration there.

## Proven Examples

- `tests/browser_sessions/conftest.py`
- `tests/browser_sessions/test_browser_session_migration_integration.py`
- `tests/browser_sessions/test_browser_session_security_integration.py`
- `tests/browser_sessions/browser_server.py`
- `apps/web/playwright.browser-session.config.ts`
- `apps/web/browser-session-e2e/session.spec.ts`
- `apps/web/browser-session-e2e/teardown.ts`
