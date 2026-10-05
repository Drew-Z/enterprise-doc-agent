# Browser Multipart Upload

## Scenario: small-file single-request upload

### 1. Scope / Trigger

New files <=1MiB use authenticated API content upload after the existing Worker
hash pass. Existing restored sessions and larger files retain the direct/multipart
flow. Local client, API, PostgreSQL and MinIO validation is not public capacity proof.

### 2. Signatures

`UploadApiClient.uploadContent(request, idempotencyKey, file, signal)` calls
`POST /api/upload-sessions/content`. `createContentIntentStore(storage)` persists
the pre-submit intent under `enterprise-doc.upload-content-intent.v1`, scoped to
the current browser tenant/actor by the application authentication transport.

### 3. Contracts

The strict intent contains only version=1, idempotencyKey, and the original create
request (filename, sizeBytes, mediaType, sha256, transport=single_put). Save it before
any content submission; a storage failure stops the request. File bytes, credentials,
signed URLs, headers and receipts never enter the intent. Whole-file base64 encoding
is allowed only after enforcing the 1MiB bound; hashing still uses the Worker.

Validate the response session identity and durable completion's session ID. Only a
completion receipt or an authoritative same-key completed session establishes success.
An unsupported route may use legacy creation with the same key; a lost response is
not unsupported. Retain the intent until completion, terminal server state, confirmed
cancellation, or successful persistence of the same legacy session.

On loss/reload, use the original metadata/key to recover creation. Completed restores
success without sending bytes; active/completing retain the intent. After reload,
require original-file reselection and hash match before a content retry. A wrong
same-name/same-size file cannot reach any content or completion request. React
StrictMode may restart an aborted recovery query with the same key. An old credential
never adopts a new enterprise during file reading or response handling.

Nginx admits at most 1,414,488 wire bytes only on the exact content route and disables
request buffering there; ordinary API limits remain unchanged. The API independently
counts the request stream before JSON parsing.

### 4. Validation & Error Matrix

| Condition | Behavior |
| --- | --- |
| Recovery save/read failure | Surface persistence error; do not submit a new request |
| Explicit unsupported 404/405 | Existing create path, same idempotency key |
| Network error/unknown receipt | Recover same-key session; no automatic content replay |
| Active/completing recovery | Original error and explicit retry, or file reselection after reload |
| Failed/aborted/expired session | Clear intent and expose terminal outcome |
| Cancellation requested for known active session | Keep intent until DELETE succeeds; recover conflicts/lost response |
| Unknown or completing submission | Do not discard it through cancel/clear/new-file actions |
| Enterprise switch | Abort current work; ignore old response and preserve scoped recovery |

### 5. Good/Base/Bad Cases

Good: a lost completed response is recovered by same-key creation and sends no second
content body. Base: explicit unsupported content endpoint continues the same legacy
session. Bad: generate a new key after an unknown response or persist an entire File.

### 6. Tests Required

`api/content.test.ts` checks HTTP bytes, bounded reading, receipt binding and enterprise
switches. `contentController.test.ts` uses the actual API client and HTTP/Worker/storage
boundaries to prove pre-submit persistence, loss/reload, wrong-file rejection, retry,
StrictMode, unsupported fallback and cancellation acknowledgment. Real browser evidence
must exercise real API/PostgreSQL/MinIO and verify document/job/quota counts; route
interception can drop a request/response but must not invent a business completion.

### 7. Wrong vs Correct

Wrong: `catch { createSession(request, crypto.randomUUID()); }`.
Correct: retain `{request, idempotencyKey}` before submission and recover that exact
intent; only a terminal response allows the UI to discard its recovery state.

## Single PUT extension (candidate, October 2026)

New browser uploads up to1MiB request `transport: single_put`. The server can return
multipart when `UPLOAD__SINGLE_PUT_ENABLED=false`; always follow the returned mode.
Larger files and callers omitting transport keep the multipart protocol. Creation
retries retain the requested mode and idempotency key; server-side replay retains the
stored transport across operational switch changes.

The strict version1 recovery record additionally permits optional `transport`.
Absent means multipart. Store it for direct sessions and reject transport changes
during reconciliation. Do not persist URLs, headers, credentials or file bodies.

Direct sessions call POST `/{id}/object/presign` and `/{id}/object/complete` under
`/api/upload-sessions`, both with an exact empty JSON object. Before XHR, validate the
allowlisted origin, unique case-insensitive headers, signed Content-Length, session
metadata and `If-None-Match: *`. Remove Content-Length from manually set headers:
the browser derives it from the Blob. R2 CORS must permit the conditional header and
four signed metadata headers, and expose ETag for the exact application origin.

A412 response alone is never success. Read the authoritative session and require an
active single_put session with matching size and one part with matching SHA-256,
size and nonempty ETag. Dispatch only through the existing generation/attempt checks;
pause or cancellation invalidates late readback. Refresh requires original-file
reselection and hash verification before completion; a wrong same-name/same-size
file must cause no upload or completion writes.

The backend performs full bounded content/hash/envelope validation. Canceled direct
uploads retain permanent zero-byte conditional retirement markers, including after
demo cleanup, to prevent delayed signed PUTs recreating canceled content.

Validation: API, state/persistence and React controller tests live beside the modules.
Real isolated PostgreSQL/API/MinIO browser checks covered normal upload, lost actual
PUT response followed by real412, and refresh/wrong-file recovery. A separate real
R2 browser probe verified signed PUT200, exposed ETag, repeated412 and unchanged
content. These checks do not establish public performance or production deployment.

## Scenario: Slice 8 hashing, transfer, state, and recovery contracts

### 1. Scope / Trigger

Use this contract when changing browser-owned multipart hashing, control-plane calls,
presigned PUT transfers, upload scheduling, pause/resume behavior, or refresh recovery.
Slice 8 owns transport-independent modules under `apps/web/src/upload`; Slice 9 owns
their React integration and real browser workflow.

### 2. Hashing Contract

- SHA-256 runs in a dedicated Web Worker through a versioned, exact-key protocol.
- The runner reads at most `min(part size, requested chunk size, 4 MiB)` per slice. It
  never calls `File.arrayBuffer()` for the complete file.
- Creation requires the whole-file lowercase hexadecimal SHA-256 before the server has
  selected a part size. The client therefore performs a bounded whole-file pass, creates
  the session, then performs a second bounded pass using the returned part size to
  produce canonical base64 per-part checksums.
- Recovery first reads session status without requesting a file. A completed receipt
  needs no hash pass. An active session still requires a bounded pass verifying
  filename, size, whole SHA-256 and part checksums before reconciling parts for transfer.
- Worker construction, startup, runtime, unreadable-message, malformed-response,
  read, hash, and cancellation failures settle the job with a typed error and terminate
  the Worker. A job cannot remain pending after a Worker protocol failure.
- The whole-file SHA-256 remains client-declared and unverified by the server in M1.

### 3. API And Direct PUT Contract

- Every control-plane call validates request and response data with strict Zod schemas.
  Session IDs are UUIDs, part numbers are safe integers from 1 through 10,000, dates are
  offset-aware ISO datetimes, and SHA-256 values use their exact hex/base64 formats.
- `UploadApiClient` requires at least one exact HTTP(S) object-store origin. Credentials,
  paths, queries, fragments, empty lists, and non-HTTP(S) schemes are rejected.
- A presign response must echo the requested part number, byte size, and checksum. It
  must contain exactly one case-insensitive `x-amz-checksum-sha256` header matching the
  requested checksum. Additional signed headers are allowed and remain opaque.
- XHR copies only the returned presign headers. It never adds the API bearer token.
  A successful 2xx response must expose a non-empty opaque `ETag`, including any quotes.
- XHR setup, header, send, HTTP, network, timeout, abort, missing-ETag, and progress
  callback failures are typed. `AbortSignal` is supported by both control-plane fetches
  and direct PUTs.

### 4. State Machine Contract

- `reduceUpload` is pure and returns `{ accepted, state, effects }`; it performs no
  Worker, fetch, XHR, scheduler, or storage calls.
- UI commands have an explicit phase legality matrix. Async events additionally require
  the current generation; part events require the current attempt and legal source
  part status.
- Pause increments the generation, resets active browser parts to pending, clears the
  queued browser work, and aborts active XHRs. It does not call the server abort route.
- Resume queues the new generation. If an old generation is still settling after abort,
  the scheduler holds the replacement behind it so the same part never uploads twice
  concurrently.
- Cancel aborts local work, clears recovery metadata, and calls server DELETE when a
  session is known. If create succeeds after local cancellation, the stale success is
  converted into a compensating server abort instead of losing the new session ID.
- A failed completion first performs one read-only session reconciliation. Completed
  restores success; active/completing retain the original error and explicit retry.
  Reads never dispatch completion a second time on this path. Cancellation/reselection
  are rejected while reconciling or when the observed session is completing. Confirmed
  failed/aborted/expired sessions release local recovery metadata without another DELETE.

### 5. Scheduling And Progress

- `PartUploadScheduler` defaults to four active part tasks.
- A queued or active part is unique within a generation. A newer generation may replace
  queued work or wait behind an older active generation, but it cannot run concurrently
  with that older task.
- Fulfilled, rejected, and synchronously thrown task outcomes release their slot and
  start the next runnable part.
- Aggregate progress is byte-weighted. Completed parts contribute their full size;
  active parts contribute bounded XHR progress; pending, presigning, and failed parts
  contribute zero.

### 6. Refresh Recovery And Privacy

The recovery record is strict version 1 and contains only:

```text
version
sessionId
filename
sizeBytes
declaredSha256
partSizeBytes
expiresAt
```

Unknown versions, invalid JSON, malformed values, and every extra field invalidate and
remove the record. JWTs use a separate session-storage key. Signed URLs, signed headers,
object keys, object-store upload IDs, bearer tokens, and file bodies are never written to
the recovery record.

After refresh, `GET /api/upload-sessions/{id}` checks server identity and status first.
A completed session clears recovery metadata and enables the next file immediately.
For an active session, reselect the original file and verify filename/size/SHA-256,
then read server-observed parts again before any `queue_parts` effect. A different
file cannot cause presign/PUT/complete; the file picker stays available to correct it
without navigation or another refresh.

The workspace offers multi-select and `webkitdirectory`, with at most 100 queue
entries and one active file. TXT/PDF/DOCX are accepted; unsupported/empty files are
reported and skipped. Every file gets a distinct idempotency key and upload session,
including equal basenames from separate folders. Relative paths are local display
labels; only `File.name` reaches the API. Pending entries can be removed and finished
entries cleared. Failure waits for explicit retry/cancel before the queue advances.
File references stay in memory and are released from finished entries; refresh
requires reselecting pending files. Starting the next file resumes the part scheduler.

### 7. React Effect Interpreter And Browser Contract

- React must not duplicate reducer legality in local booleans. A controller applies
  actions through `reduceUpload` and interprets only the returned effects.
- The scheduler is resumed during effect setup and paused during cleanup. This preserves
  real unmount cancellation while remaining correct under React StrictMode's development
  setup/cleanup/setup cycle.
- Store an injected or native fetcher, but invoke it as a plain function. Calling a
  native browser fetch as `this.fetcher(...)` binds the API client as the receiver and
  can fail with `Illegal invocation` before network I/O.
- The local token control uses the isolated token store. The upload workspace never
  displays or persists signed URLs, signed headers, object keys, upload IDs, or part
  checksums.
- A real browser recovery test must use PostgreSQL, the API, and MinIO, not route-mocked
  control-plane responses. It may delay direct PUT continuation to make pause timing
  deterministic, but the resumed PUT and completion must reach the real object store.
- Desktop and mobile evidence must assert horizontal fit and non-overlap of the main
  operational bands in addition to taking screenshots.

### 8. Tests Required

- Hash boundaries, final short part, 4 MiB read cap, progress, cancellation, short reads,
  read failures, strict request/response protocol, Worker runtime, and Worker lifecycle.
- Exact API schemas, error envelope, path validation, AbortSignal, presign echo/header
  binding, and fail-closed object-store origin validation.
- XHR exact headers, opaque ETag, progress clamping, setup/send/event failures, timeout,
  and pre/runtime abort.
- Full reducer phase-command matrix, generation/attempt/status rejection, all retry
  targets, pause/cancel distinction, create-cancel compensation, part-plan validation,
  reconciliation, and different-file rejection before network effects.
- Ten queued scheduler tasks proving the four-way cap, slot release on success/failure,
  pause behavior, duplicate rejection, and new-generation wait-behind behavior.
- Exact persistence keys, invalid-version/extra-field rejection, secret scanning, and
  isolated token storage.
- Testing Library coverage for local token gating, complete two-pass upload, StrictMode
  scheduler activation, recovery restore, and same-name/same-size content mismatch.
- Playwright coverage for real session creation, presign, held PUT pause, reload,
  wrong-content rejection before presign, immediate reselection, missing-part reconciliation,
  completion, 1440x900 and 390x844 screenshots, overflow, and major-band overlap.
- Inject one Worker load failure and one lost response after real completion. Assert
  explicit retry, read-only completion recovery, subsequent queue progress, independent
  versions for same-named folder files and no horizontal overflow. At mobile widths,
  file pickers must not inherit a 320px vertical flex basis.

Run:

```powershell
pnpm --filter web test -- src/upload
pnpm --filter web lint
pnpm --filter web typecheck
pnpm --filter web build
pnpm --filter web exec playwright test
```

### 9. Wrong vs Correct

#### Wrong

```ts
sessionStorage.setItem("upload", JSON.stringify({ token, signedUrl, file }));
```

This persists secrets and attempts to retain a browser file handle across refresh.

#### Correct

Persist only the strict recovery record, require explicit file reselection, verify
filename/size/hash, reconcile server-observed parts, and issue fresh presigns only for
missing parts.

#### Wrong

```ts
pauseUpload();
await api.abortSession(sessionId);
```

Pause is a browser execution control, not a destructive server transition.

#### Correct

Abort local XHRs and clear queued browser work on pause. Call server DELETE only for the
explicit cancel command.

## Proven Examples

- `apps/web/src/upload/hashing/runner.ts`: bounded incremental whole-file and per-part
  hashing used by the Worker runtime.
- `apps/web/src/upload/state/reducer.ts`: pure legal-transition and effect contract,
  including generation-aware pause, resume, retry, cancel, and recovery.
- `apps/web/src/upload/controller.ts`: React-facing effect interpreter and scheduler
  ownership without duplicating reducer legality.
- `apps/web/e2e/upload-recovery.spec.ts`: real PostgreSQL/API/MinIO interrupted-refresh
  recovery, wrong-content rejection, completion, and responsive layout evidence.
- `scripts/multipart_smoke.py`: generated direct multipart transfer, API restart/resume,
  completion replay, and API RSS observation outside the browser path.
