# Public demo workspaces

## 1. Scope / trigger

Applies to anonymous demos, their HTTP capability boundary, cost/storage limits and automatic expiry. Demo sessions are separate from verified OIDC/GitHub identities. A public shared password or synthetic email verification is not used.

## 2. Signatures

- `DemoService.start/get/logout`: isolated tenant creation, opaque session discovery and revocation.
- `POST /auth/demo {}`: same-origin start or resume; `GET /auth/session`: typed discovery.
- `GET /api/demo`: context-bound attempt and upload limits.
- `check_upload/check_packet/reserve_attempt/finish_attempt`: atomic domain limits.
- `DemoCleanupService.run_once()`: retirement, safe object reclamation, then database deletion.
- `configure_staging_manifest.py --demo-enabled true|false`: binds `DEMO__ENABLED`; workflow environment is `STAGING_DEMO_ENABLED`.

## 3. Contracts

Default disabled. Enabling through deployment requires browser sessions and presales generation. Anonymous metadata advertises `demoAvailable: true`. Guest metadata has `demo: true`, `email: null`, immutable context/CSRF, selected temporary tenant and absolute expiry. No `VerifiedAdmissionIdentity`, external binding, admission grant, invitation or reusable bearer is created. The internal `guest-<uuid>@demo.invalid` User email is only a database identifier and is never displayed as verified or used for mail.

Cookie format and CSRF reuse the existing opaque browser transport. The separate database digest identifies a demo. Formal identity endpoints cannot load it. Existing formal sessions cannot be overwritten through the demo entry. All business requests first validate the demo session, then match an explicit method/path allowlist limited to uploads, document inventory, job reads/cancel, presales, session and usage. No management or Agent gateway access is implied by the temporary owner's domain role.

Every visitor gets an isolated tenant. Defaults: two hours, 6 files, 2 MiB each, 10 MiB combined, 3 sheets and 6 rows each. The lifetime attempt count is 6; failures/cancellations count. A global PostgreSQL advisory transaction lock enforces at most 6 retained workspaces, 24 creations/UTC day, 40 attempts/UTC day and one live demo generation. Existing tenant locks precede quota checks; idempotent replays occur before charging. Success/failure releases only the concurrent lease, never the hard attempt counter. A stale attempt cannot clear a newer lease.

`browser_identity_smoke.py` accepts exactly the optional `demoAvailable: true` anonymous variant with the matching explicit `loginProvider`. It records `public_demo_advertised`, without creating a guest, calling a model or claiming an authenticated journey. Wrong providers, non-boolean flags and unexpected fields remain rejected.

Cleanup runs inside the existing API lifecycle, including when new demo admission is disabled but browser auth remains on. It retires tenant/membership activity, requests job cancellation, waits at least one hour beyond expiry/revocation (the maximum configured presign TTL), and skips live generation/job leases. It locks job records against new claims, verifies dedicated guest ownership and each canonical upload key/metadata. It aborts incomplete multipart uploads, deletes owned objects, breaks the upload/version RESTRICT cycle, deletes documents/uploads/tenant/user, and retains a sanitized receipt for seven days. Failed deletion preserves database ownership for retry. Daily limits survive workspace deletion. Existing enterprise objects are outside the cleanup scope.

## 4. Errors

| Condition | Result |
| --- | --- |
| Disabled entry | 503 `demo_unavailable` |
| Wrong origin / missing mutation CSRF | Existing browser 403 contract |
| Expired/revoked demo | Session discovery anonymous; business 401 |
| Stale or another visitor's context | 409 `browser_context_stale` |
| Formal account already signed in | 409 `demo_signout_required` |
| Administrative/unbudgeted route | 403 `demo_operation_forbidden` |
| Space, upload, sheet, lifetime or daily limit | Fixed 429 `demo_*` code and human-readable UI |
| Another demo generation active | 429 `demo_generation_busy`, explicit retry only |
| Unknown object ownership / storage unavailable | No DB deletion; sanitized retry log |

## 5. Good / base / bad

- Good: a visitor uploads the public sample files through the normal uploader, generates with the configured gateway, checks citations and exports reviewed CSV.
- Base: feature off, existing GitHub login and enterprise scopes continue unchanged.
- Bad: `?showcase=1`, prefilled model answers or forged verified email presented as the live demo; retrying failed generations without charging the hard budget.

## 6. Required tests

Real PostgreSQL: distinct visitors, expiry/revocation, stale context, concurrent creation capacity, daily/lifetime attempts including failure, global concurrency, idempotence, migration round trip, object ownership mismatch, retryable deletion and the real FK cycle. HTTP: same-origin/CSRF, cookie flags, mixed credentials, denied formal endpoints, disabled entry. UI: opt-in anonymous entry, safe failure recovery, no management navigation, sample download, limits and unchanged foreground sessions. Real Chromium acceptance must upload/parse actual sample bytes, call the configured external model, review citations and download CSV; record local and public acceptance separately.

## 7. Wrong vs correct

Wrong: delete tenant rows before objects, refund failed demo model calls, infer guest trust from an email string, or create an anonymous session for a caller already signed in to an enterprise.

Correct: independent session type with a closed capability list, immutable tenant-scoped transport, counters committed before dispatch, bounded real work, and verified object cleanup before row deletion.

## Proven Examples

- `packages/core/src/enterprise_doc_core/demo/service.py`: guest, enterprise and entitlement binding.
- `apps/api/src/enterprise_doc_api/browser_auth/demo.py`: same-origin entry and route boundary.
- `apps/web/src/demo/PublicDemoWorkspace.tsx`: real uploads, source samples and presales flow.
- `tests/demo/`: PostgreSQL lifecycle, ownership, HTTP and cleanup integration tests.
