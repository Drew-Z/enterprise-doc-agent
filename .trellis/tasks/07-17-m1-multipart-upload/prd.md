# M1 Multipart Upload

## Goal

Deliver the first tenant-scoped business workflow in the Enterprise Document Agent
Platform: an authenticated user can create a resumable upload session, transfer TXT,
PDF, or DOCX parts directly to MinIO/S3, recover after interruption, and complete the
upload into one durable `DocumentVersion` without sending the file body through the
FastAPI request process.

M1 must produce executable evidence for correctness, tenant isolation, restart/resume,
idempotency, cleanup, and bounded API memory. It must not claim that document parsing,
durable ingestion jobs, or production-scale capacity already exist.

## Background

- M0 established a locked uv/pnpm monorepo, typed settings, PostgreSQL/Redis/MinIO,
  Alembic, API/Worker/Web health, secret-safe JSON logs, request/correlation context,
  optional OTel bootstrap, CI quality checks, and a reproducible foundation smoke path.
- M0 deliberately leaves `RequestContext.principal` empty. M1 is the first milestone
  allowed to expose tenant-scoped endpoints, so it must resolve a signed bearer token
  and revalidate an active persisted membership before any upload query or mutation.
- PostgreSQL remains the business source of truth. MinIO/S3 owns bytes and multipart
  state, but object-store state alone never establishes tenant ownership or business
  completion.
- M1 ends with an uploaded `DocumentVersion`. M2 owns `Job`, `OutboxEvent`, Celery,
  claim, lease, heartbeat, fencing, retries, and DLQ. The atomic
  upload-complete-to-ingestion-job gate is therefore completed jointly after M2.

## Requirements

### Identity and tenant isolation

- **R-1**: M1 introduces persisted `Tenant`, `User`, and `Membership` records plus a
  real principal resolver. The API validates a signed JWT bearer token, validates its
  issuer, audience, expiry, subject, and tenant claim, and then queries PostgreSQL for
  an active tenant, user, and membership. Token claims alone never authorize access.
- **R-2**: Every upload and document query includes the resolved tenant boundary and,
  where ownership is required, the creating actor. Cross-tenant IDs, inactive
  memberships, and actor-mismatched upload sessions fail without revealing whether the
  target resource exists.
- **R-3**: A development bootstrap command may create a local tenant/user/membership
  and issue a short-lived local token. It is not a production login flow, is forbidden
  outside local/test environments, and never places a token in tracked configuration,
  normal logs, traces, or evidence artifacts.

### Upload creation and validation

- **R-4**: `POST /api/upload-sessions` requires an `Idempotency-Key` and accepts a
  filename, byte size, declared media type, and lowercase hexadecimal whole-file
  SHA-256. A replay with the same tenant, key, and normalized request returns the same
  session; the same key with different input returns a typed conflict.
- **R-5**: Creation accepts only `.txt`, `.pdf`, and `.docx` with their allowed media
  types. It rejects empty files, configured size-limit violations, path separators,
  control characters, unsafe/reserved names, extension/media mismatches, malformed
  hashes, impossible part counts, and exhausted tenant quota before issuing upload
  credentials.
- **R-6**: Tenant quota reservation is concurrency-safe. Creating a session reserves
  its declared size exactly once; completion converts the reservation to used bytes;
  abort, expiry, and terminal initialization failure release it exactly once.
- **R-7**: Part size is server-selected, respects S3 multipart constraints, and keeps
  the part count at or below 10,000. Object keys are random, server-owned identifiers
  and never contain a user filename, tenant name, email address, or host filesystem
  path.

### Direct multipart transfer and resume

- **R-8**: The API creates S3-compatible multipart uploads and returns short-lived,
  least-scope presigned `UploadPart` URLs. Signed URLs are never persisted in the
  database or emitted to normal logs/traces. The browser receives no object-store
  credentials.
- **R-9**: Before a part URL is signed, the client supplies a base64 SHA-256 checksum
  for that exact part. The checksum is signed into the request and recorded as the
  expected part checksum. A retry for the same part must use the same content checksum.
  A different checksum is rejected because an already issued presigned URL remains
  usable until expiry; changing the expectation requires aborting/restarting the upload
  generation rather than an unsafe in-place replacement.
- **R-10**: `GET /api/upload-sessions/{id}` reconciles the tenant-owned database row
  with `ListParts`, paginates correctly, and returns only verified part number, size,
  ETag, and checksum metadata required to resume. Missing parts can be uploaded in any
  order, while checksum-enabled completion requires a consecutive sequence starting
  at part 1. A listed checksum or size mismatch invalidates an earlier observation;
  absence from one complete listing is not destructive evidence within the same
  multipart generation, and stale concurrent listings cannot overwrite newer evidence.
- **R-11**: The local open-source MinIO profile permits only configured Web origins and
  exposes `ETag` plus required checksum headers so the browser can complete the
  protocol. Its community image lacks `PutBucketCors`, so local evidence uses an exact
  server-level origin list and records that limitation; production S3/AIStor uses
  per-bucket rules. Wildcard production CORS is not introduced.

### Completion, integrity, and document state

- **R-12**: `POST /api/upload-sessions/{id}/complete` verifies the caller, session
  state, expiry, client-maintained ordered part list, object-store `ListParts` result,
  expected part sizes, ETags, and per-part SHA-256 values before completing the
  multipart upload.
- **R-13**: Completion tolerates retries and a crash between object-store completion
  and database finalization. If the multipart upload no longer exists, the API
  reconciles the expected random object key with `HeadObject` and metadata before
  deciding whether to finalize, reject, or clean up.
- **R-14**: Completion verifies object size, server-owned metadata, media signature,
  and a bounded file-envelope policy. PDF must have a PDF signature; TXT samples must
  not contain NUL bytes and must be valid UTF-8; DOCX must be a valid ZIP envelope with
  required Office entries, bounded central-directory bytes, entry count, declared
  uncompressed size, compression ratio, and safe member paths. M3 repeats streaming
  decompression limits while parsing.
- **R-15**: Transport integrity uses object-store-validated per-part SHA-256 and the
  multipart checksum contract supported by the pinned MinIO/S3 implementation. The
  browser-declared whole-file SHA-256 is persisted as an unverified content identity;
  M3 recomputes it while reading the document and is the milestone that may mark it
  verified. M1 must not label the declared whole-file hash as server-verified.
- **R-16**: A successful completion creates exactly one `Document`, one
  `DocumentVersion`, and one durable link from the upload session. Repeated or
  concurrent completion returns that same version and never increments quota twice.
  The version state is `uploaded`, not `ready`.

### Abort, expiry, and cleanup

- **R-17**: `DELETE /api/upload-sessions/{id}` is idempotent for active/aborted
  sessions, aborts the object-store multipart upload when present, releases quota, and
  never deletes a completed document version.
- **R-18**: An executable cleanup command processes expired/failed database sessions
  in bounded batches, reconciles stale `completing` rows, aborts incomplete uploads,
  releases reservations, and detects old object-store multipart uploads whose random
  keys no longer map to a database session. Cleanup is safe to rerun and records counts
  and error classes without object keys, signed URLs, filenames, or document bodies.

### Web experience

- **R-19**: The operational Web UI supports token entry for the local development
  principal, file selection, hashing progress, session creation, part progress, four
  bounded concurrent uploads by default, pause, resume, per-part retry, completion,
  cancel, and clear completed state. Familiar Lucide icons, accessible labels, and
  stable desktop/mobile layout are required.
- **R-20**: Incremental hashing runs in a Web Worker and keeps memory bounded by the
  configured part size. The browser does not read a large file into one ArrayBuffer.
- **R-21**: Refresh recovery persists only non-secret upload metadata. After refresh,
  the user reselects the local file; the client verifies filename, size, and declared
  SHA-256, fetches server-observed parts, and uploads only missing parts. Tokens remain
  in session storage; signed URLs are never persisted.
- **R-22**: The upload state machine has explicit legal transitions and typed failure
  categories. Pausing aborts current browser requests without aborting the server
  session; canceling invokes the server abort endpoint.

### Operations, tests, and evidence

- **R-23**: Authenticated request logs and spans include tenant/actor identifiers only
  after successful principal resolution. Logs/traces exclude authorization headers,
  JWTs, signed URLs, object-store upload IDs, object keys, filenames, hashes when they
  can identify content, request bodies, and raw dependency exceptions. Log messages are
  stable event names rather than parameterized dependency or document data.
- **R-24**: Unit, API contract, PostgreSQL/MinIO integration, browser, tenant-isolation,
  idempotency, cleanup, and failure-recovery tests are deterministic and cannot be
  hidden with skip, xfail, rerun-to-green, or allow-failure CI behavior.
- **R-25**: A reproducible M1 smoke uploads a generated 1 GiB file through presigned
  parts, interrupts and resumes it, retries completion, and records API process memory
  samples. The evidence reports observed values and limitations; it does not promote
  this one run to a production capacity claim.
- **R-26**: M1 writes an immutable evidence manifest under `evidence/m1/`, updates the
  parent evidence index, records the reviewed commit and exact successful commands,
  and stores logs/reports/screenshots with SHA-256 digests.

## Constraints

- Use the existing Python 3.12, FastAPI, SQLAlchemy 2 async, Alembic, boto3, React 19,
  TypeScript, TanStack Query, Vitest, Playwright, uv, and pnpm foundation.
- Keep API, Worker, and Web separately runnable. API and Worker may import Core but may
  not import each other.
- Boto3 control-plane calls must not block the asyncio event loop; direct file bytes
  travel browser-to-object-store.
- Applied migration `20260717_0001_enable_vector.py` is immutable. M1 adds a new
  migration with a reversible downgrade while no later milestone depends on it.
- Configuration must distinguish the internal object-store endpoint from the endpoint
  used to generate browser-reachable signatures.
- Tests may use local development credentials only in local/test mode.
- Measured results, target values, and known limitations must remain visibly distinct.

## Out Of Scope

- Production login, password reset, OAuth/OIDC federation, refresh-token rotation,
  Row-Level Security, or public tenant administration.
- `Job`, `JobAttempt`, `JobEvent`, `OutboxEvent`, Celery, claim, lease, heartbeat,
  fencing, retry budget, cancellation, or DLQ behavior.
- TXT/PDF/DOCX text extraction, content deduplication, chunking, embeddings, pgvector
  indexes, Hybrid RAG, citations, or full-file hash verification during parsing.
- LangGraph, MCP, model calls, SSE run streams, approvals, or downloadable artifacts.
- Kubernetes, public staging, production object-store policy, image publication,
  full load testing, or claims about sustained production throughput.

## Acceptance Criteria

- [x] An invalid/missing/expired JWT returns a typed 401; an inactive or mismatched
  persisted membership returns a typed 403/404 boundary response; valid requests add
  the revalidated tenant/actor to request context, logs, and spans without token leaks.
- [x] Concurrent create requests with one idempotency key and identical input produce
  one upload session and one quota reservation; a conflicting payload returns 409.
- [x] Filename, extension, media type, size, hash, quota, part-count, and path-safety
  policy tests cover allowed TXT/PDF/DOCX plus representative invalid inputs.
- [x] A real MinIO integration proves create, checksum-bound presign, direct part PUT,
  list/resume aggregation, complete, head verification, and abort; deterministic adapter
  tests prove all-page pagination behavior.
- [x] Refresh/resume uploads only missing parts after the same file is reselected and
  rejects a different file before issuing any new part URL.
- [x] Duplicate and concurrent completion return the same `DocumentVersion`; database
  counts and quota counters prove one effective side effect.
- [x] A simulated crash after S3 completion but before database finalization is
  reconciled on retry without creating a duplicate version or losing the object.
- [x] PDF/TXT/DOCX envelope tests reject signature mismatch, unsafe ZIP paths, excess
  entries, excess declared expansion, excessive compression ratio, and missing DOCX
  required entries using bounded range reads.
- [x] Abort, expiry, stale-completion reconciliation, and unknown multipart cleanup are
  safe to rerun and release only the correct reservation.
- [x] Web unit tests cover legal state transitions, four-way scheduling, pause/resume,
  retry, cancellation, worker hashing messages, runtime API validation, and secret-free
  persistence.
- [x] Playwright verifies the authenticated upload workflow, interrupted refresh
  recovery, and stable desktop/mobile layouts without overlap.
- [x] The generated 1 GiB smoke completes and records API RSS samples showing that the
  file body was not buffered by FastAPI. The report states the observed environment and
  does not claim production capacity.
- [x] Backend format/lint/typecheck/unit, frontend lint/typecheck/unit/build,
  PostgreSQL/MinIO integration, M1 smoke, evidence contracts, and Trellis validation
  pass from exact recorded commands.
- [x] No M2-M7 behavior is represented as implemented or measured.

## Notes

- The source plan described completion and ingestion-job creation together. The parent
  task's reviewed milestone boundary is authoritative: M1 persists the uploaded version;
  M2 extends that transaction with `Job` and `OutboxEvent`, then the parent runs the
  joint atomicity gate.
- S3 multipart ETags are not treated as whole-file MD5 values.
- A 1 GiB local smoke is evidence for one run on one machine, not a load test.
- The remote `m1-integration` workflow is defined but has not been executed in this
  local evidence cycle; no remote CI or deployment result is claimed.
