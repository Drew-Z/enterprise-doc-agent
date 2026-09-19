# Browser Sign-in and Enterprise Workspace Lifecycle

## Scope / Trigger

Use this contract when changing login/selection/admission/logout UI, any business
client, browser credentials, cross-tab synchronization, recovery or direct transfers.
Browser mode is the default. `VITE_AUTH_MODE=bearer` explicitly enables the older
developer harness; showcase remains independent. Disabled/unavailable browser auth
never reads a saved bearer as a fallback.

## Signatures

- `BrowserSessionBoundary`, `BrowserSessionController`: session discovery and UI lifecycle.
- `ApiCredential = string | BrowserCredential`, `authenticatedFetch(...)`:
  one transport for business HTTP, SSE and download metadata.
- `createApplicationCredentialStore(...)`: capture the current in-memory credential.
- `createSessionEvents(...)`: native BroadcastChannel with storage-marker fallback.
- `consumeAuthEntry()`: read a supported URL fragment once before React renders.
- `scopedRecoveryKey(...)`: existing recovery key plus tenant and actor IDs.

## Contracts

The immutable browser credential contains context, CSRF, tenant/actor IDs and an
abort signal. It is never stored or broadcast. A client captures one object; old
clients cannot adopt a new enterprise's credential. Requests are same-origin under
`/api/`, with `credentials=same-origin`, no-store and redirects rejected. Reads
carry context; mutations carry CSRF as well. Callers cannot supply authentication
headers. Explicit bearer mode uses `credentials=omit`. Call native fetch unbound.

The transport rejects responses arriving after retirement and combines operation
and session abort signals. 401 and session-specific 403/409 errors invalidate the
workspace; an ordinary document ACL denial does not sign the user out. No failed
business write is automatically replayed. All product, audit/governance, identity,
membership, presales, Agent/SSE/artifact and upload-control clients use this boundary.

Selection/logout/remote invalidation retires credentials, clears queries, unmounts
workspace state and aborts hash/PUT/SSE/control operations. The chooser uses server
enterprise names and roles, not user-entered UUIDs. A switch is shown as successful
only after its returned enterprise is verified. Logout closes the workspace first;
an unconfirmed response remains closed until explicit retry or verification.

BroadcastChannel carries only the string `invalidate`. When unavailable, storage
carries only a random UUID change marker. Returning to a visible page and pageshow
verify the server session. During an ordinary foreground verification the existing
workspace stays mounted but hidden and inert. An exactly unchanged session restores
it without losing drafts; a changed identity/role/context or failure retires it.
Merely hiding a tab does not discard edits or cancel an upload. Remote invalidation
and absolute expiry still close the workspace immediately when handled.

Upload and Agent recovery are partitioned by tenant/actor. Retain only previously
allowed metadata; presales retains only its partitioned sheet ID. An old recovery
store remains bound to its original partition. No body, email, context, CSRF,
upstream token, signed URL or cookie belongs in persistence.

Admission submits exactly a code and company name. A valid fragment code is copied
to memory and removed from history immediately. It is cleared from the input after
confirmed acceptance. An anonymous user signs in, then reopens the original link;
cross-login secret persistence is not implemented. A successful admission refreshes
the enterprise list and requires explicit selection.

Direct object PUT does not use `authenticatedFetch`. XHR explicitly disables
credentials and rejects application authentication headers and URL userinfo. In
browser mode a same-origin object URL is rejected because same-origin XHR can send
cookies even with `withCredentials=false`. Use a separate approved object origin.

## Validation & Error Matrix

| State / failure | Observable behavior |
|---|---|
| Anonymous / disabled / unavailable | Sign-in, administrator guidance or explicit retry |
| Authenticated but unselected | Authorized enterprises or empty-list/admission UI |
| Expired / server-revoked session | Workspace closed; reauthentication entry |
| Switch result unknown | Workspace closed; explicit verification, no automatic write retry |
| Logout result unknown | Closed workspace and retry/verify controls; background checks cannot restore |
| Same foreground session | Hidden/inert during check; original edits and credential preserved |
| Foreign business URL or old credential | Rejected before a new request can use it |
| Direct object transfer | No Cookie, Authorization, context or CSRF headers |

## Good / Base / Bad

- Good: select a named company, upload, refresh, switch in another tab and discard
  the old company's delayed response.
- Base: valid login with no bindings presents admission without business access.
- Bad: read a new global credential from an old callback, carry cached evidence
  between companies or declare a failed logout completed.

## Tests Required

Vitest covers transport modes, ordinary ACL vs session denial, captured credentials,
client-family coverage, recovery partitions, hash/PUT retirement, chooser/admission,
unconfirmed logout and same-session foreground preservation. Chromium must use the
real local API/PostgreSQL and signed HTTP IdP for login, admission, refresh, two-tab
switch/logout and a delayed real response. Check actual object request headers and
desktop/narrow-screen fit. Initiated requests canceled before sending are recorded
separately from responses; a missing Cookie is only acceptable with an observed
`net::ERR_ABORTED` and no response.

## Wrong vs Correct

Wrong: copy a developer token to browser storage when OIDC is unavailable, reuse
the current global tenant in a delayed request or clear a draft on every tab focus.

Correct: capture the session, fail closed on identity changes, and preserve the
mounted workspace only when server verification confirms the complete same session.

## Proven Examples

- `apps/web/src/auth/BrowserSessionBoundary.tsx`
- `apps/web/src/auth/sessionController.ts`
- `apps/web/src/auth/transport.ts`
- `apps/web/src/auth/clientCoverage.test.ts`
- `apps/web/src/auth/recovery.test.ts`
- `apps/web/src/upload/UploadWorkspace.test.tsx`
- `apps/web/browser-session-e2e/session.spec.ts`
