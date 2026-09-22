# Browser Multipart Upload

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
