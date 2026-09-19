# Browser Identity and Enterprise Sessions

## Scope / Trigger

Use this contract when changing browser OIDC, tenant selection, admission HTTP,
cookie authentication, session rotation or authorization of long-lived responses.
API owns the external protocol and HTTP. Core owns durable identity/session state.
The existing local and external machine bearer resolvers remain separate.

## Signatures

- `BrowserAuthSettings`: disabled by default; one explicit issuer, authorization,
  token and JWKS endpoint, client ID and fixed `web_origin + /auth/callback`.
- `OidcClient.authorization_url(...)`, `exchange(...)`: Authorization Code + PKCE
  S256 and verified `VerifiedAdmissionIdentity`, without tenant/group authority.
- `BrowserSessionService.begin_login`, `claim_login`, `complete_login`,
  `get_session`, `list_tenants`, `select_tenant`, `authorize`, `logout`.
- `GET /auth/login`, `/auth/callback`, `/auth/session`, `/auth/tenants`;
  `POST /auth/tenant`, `/auth/logout`, `/auth/admission/accept`.

## Contracts

URLs are exact, without userinfo/query/fragment. Enabled configurations require
HTTPS except local/test loopback. A confidential client secret is required outside
local/test. OAuth Basic credentials are form-encoded before Basic encoding. There
is no discovery, caller-supplied redirect, network retry or redirect following.
Token/JWKS exchanges have a total timeout and bounded response size. PyJWT with
the crypto extra verifies RS256/ES256, key selection, issuer/audience, iat/exp/nbf,
nonce and azp. Verified email must be literal true. Access/refresh/ID tokens are
transient and never returned to the browser or saved as credentials.

State, nonce and PKCE verifier are independent 256-bit random values; only digests
are stored. The verifier is an HttpOnly login cookie. Claim locks and consumes the
attempt before network exchange. Completion rechecks database time and the
originating session generation, preventing logout/switch from being undone by a
late callback. A new login supersedes the attempt identified by its existing login
cookie. If that attempt already produced a still-current session whose cookie has
not arrived, the old result is revoked. This does not claim to serialize first-time
parallel logins without any shared pre-existing cookie.

`bss1_` credentials carry 256 random bits and persist only as SHA-256. Cookies are
`__Host-docagent-session` and `__Host-docagent-login`, Secure, HttpOnly, Path=/,
SameSite=Lax, with no Domain. Login TTL defaults to five minutes; session TTL is
eight hours absolute. Selection rotates credential/generation without extending
expiry; successful reauthentication clears selection and starts a new lifetime.
`contextVersion` is session-family UUID plus generation, not generation alone.
CSRF is HMAC-SHA256 of the opaque cookie with a fixed domain-separated message.

An unselected identity has no business principal. Selection and every later
authorization join active Tenant/User/Membership/exact Binding. Current role comes
from Membership. The identity-to-user ambiguity check includes inactive bindings;
more than 1000 matching bindings fails closed. Email equality alone never creates
a binding. Historical selected UUIDs have no business cascade foreign keys, so
deletion/recreation cannot silently revive an old selection.

Cookie `/api/` requests, including reads and SSE, require `X-Session-Context`.
Mutations additionally require exact configured Origin and `X-CSRF-Token`.
Duplicate authentication cookies/security headers and simultaneous bearer/cookie
credentials are rejected. Neither invalid credential mode falls back to the other.
SSE rechecks the complete principal before each batch and each business event.
Ending a stream or aborting a browser request does not undo an already committed
operation. Previously signed object URLs retain their original short TTL.

Admission accepts only `token` and `tenantName`; identity comes from the verified
session and calls the real admission service. No bootstrap or membership-provision
shortcut is used. Successful admission does not automatically select an enterprise.

Lifecycle events commit with the session mutation, uniquely per family/generation.
They contain action and optional tenant/user IDs, not email, subject, token or
arbitrary metadata. Request/correlation linkage and expiry retention are not yet
an operational policy. There is no automatic purge.

## Validation & Error Matrix

| Condition | Result |
|---|---|
| Missing/invalid/expired/revoked credential | 401; session discovery returns anonymous |
| Inactive or replaced selected identity entities | 403 |
| Stale context, ambiguous identity, lock contention | 409 |
| Invalid login attempt / login creation rate exceeded | 400 / 429 with Retry-After 60 |
| Invalid CSRF/Origin/fetch site | 403, including non-ASCII malformed inputs |
| Duplicate or mixed credential modes | 400 |
| OIDC verification/exchange failure | Fixed sign-in failure redirect; no provider text |
| Database service unavailable | Safe 503 without SQL parameters |

`/auth/` and `/api/` use no-store, no-referrer and nosniff. Anonymous session
discovery does not emit a stale cookie deletion. Login callbacks do not delete a
potentially newer login cookie. Uvicorn access logs and proxy-header inference are
disabled at the application entry point; login rate accounting uses the observed
peer, so a reverse proxy may aggregate users until a trusted deployment-specific
addressing/rate policy is deliberately introduced. Auth-location Nginx access
logging is disabled. Trace URL/query/target attributes are sanitized.

## Good / Base / Bad

- Good: verified subject with explicit active bindings selects a named enterprise;
  each request and later SSE event revalidates that selection.
- Base: a newly verified user has no enterprise and may present an admission code.
- Bad: same-email linking, role/group claim authority, trusting a posted identity,
  replaying a consumed callback or allowing a stale context after rotation.

## Tests Required

Use signed ID tokens, real PostgreSQL locks/time and actual HTTP contracts. Cover
protocol errors/replay, login replacement, reauthentication/logout/selection races,
identity ambiguity/deletion, expiry after lock waits, CSRF/mixed/duplicate inputs,
admission and per-event SSE revocation. Keep machine bearer regression coverage.
Migration 0024 is additive after 0023. Test schemas explicitly create prior tables
with `checkfirst=False`; never reset public. Snapshot old public rows around local
upgrade and preserve earlier delivery manifests.

## Wrong vs Correct

Wrong: accept an ID-token payload without signature checks, select an enterprise
from an email or claim, or treat closing a page as confirmed server logout.

Correct: verify the protocol outside the database transaction, authorize through
explicit database relationships, rotate under locks and report logout only after
the durable revoke succeeds. Rollback disables browser authentication and retains
data; production revocation, TLS, provider and retention operations need their own
reviewed deployment procedure.

## Proven Examples

- `apps/api/src/enterprise_doc_api/browser_auth/oidc.py`
- `apps/api/src/enterprise_doc_api/browser_auth/http.py`
- `apps/api/src/enterprise_doc_api/browser_auth/router.py`
- `packages/core/src/enterprise_doc_core/browser_sessions/service.py`
- `packages/core/src/enterprise_doc_core/db/migrations/versions/20260913_0024_browser_sessions.py`
- `tests/browser_sessions/test_browser_login_replacement_integration.py`
- `tests/browser_sessions/test_browser_auth_http_integration.py`
- `apps/api/tests/test_api_telemetry.py`
