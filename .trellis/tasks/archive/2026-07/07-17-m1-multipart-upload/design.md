# M1 Multipart Upload: Technical Design

## Design Summary

M1 adds a tenant-aware upload control plane around PostgreSQL and MinIO/S3 while the
file data plane remains browser-to-object-store:

```text
React Web
  |-- Authorization: Bearer JWT --> FastAPI upload control APIs
  `-- presigned UploadPart PUT --> MinIO/S3

FastAPI
  |-- verify JWT + reload active membership --> PostgreSQL
  |-- create/list/complete/abort multipart --> MinIO/S3
  `-- create uploaded DocumentVersion --> PostgreSQL
```

The API handles metadata and bounded range inspection only. It never receives a
multipart/form-data file body and never proxies a complete file upload.

## Existing Foundation And Ownership

- `packages/core` already owns typed infrastructure settings, request context, async
  SQLAlchemy engine creation, health adapters, structured logging, and OTel bootstrap.
- `apps/api` owns FastAPI routes, middleware, app lifecycle, and HTTP response mapping.
- `apps/web` owns the operational UI, typed network boundaries, and client-only state.
- M1 adds shared persisted identity/document models and the S3 adapter to Core because
  later Workers need those contracts. JWT HTTP extraction and upload routes remain API
  owned. Upload UI and browser transfer logic remain Web owned.

Proposed layout:

```text
packages/core/src/enterprise_doc_core/
  db/base.py
  db/session.py
  identity/models.py
  documents/models.py
  storage/multipart.py
  uploads/policy.py
  uploads/types.py

apps/api/src/enterprise_doc_api/
  auth/dependencies.py
  auth/jwt.py
  errors.py
  uploads/router.py
  uploads/schemas.py
  uploads/service.py

apps/web/src/
  api/uploads.ts
  upload/UploadWorkspace.tsx
  upload/machine.ts
  upload/transfer.ts
  upload/hash.worker.ts
  upload/persistence.ts
```

No generic repository or service framework is introduced. Feature modules own their
queries and expose only contracts reused by more than one layer.

## Identity Contract

### Persisted records

- `Tenant`: UUID, normalized name/slug, active flag, quota bytes, used bytes, reserved
  bytes, timestamps.
- `User`: UUID, normalized email, active flag, timestamps.
- `Membership`: UUID, tenant ID, user ID, role (`owner` or `member`), active flag,
  timestamps, unique `(tenant_id, user_id)`.

### Bearer token

Local/test tokens use signed JWT HS256 with required `iss`, `aud`, `sub`, `tenant_id`,
`iat`, `nbf`, `exp`, and `jti` claims. The signing key is a secret-aware API setting.
Non-local environments reject the known development key.

Resolution steps for every business request:

1. Parse exactly one `Authorization: Bearer` value.
2. Verify algorithm, signature, issuer, audience, time claims, UUID claims, and a bounded
   token length.
3. Query active `Tenant`, `User`, and `Membership` by both UUID claims.
4. Build `PrincipalContext` from persisted IDs/role, enrich the current request context,
   and add only safe IDs to the active span.
5. Execute every resource query with `tenant_id`; actor ownership is checked for
   upload-session mutations.

Authentication failure uses 401 with `WWW-Authenticate: Bearer`. Valid authentication
without a current membership uses 403. A resource outside the principal boundary uses
404 to avoid an existence oracle.

## Database Model

### Document and upload tables

`Document`

```text
id UUID PK
tenant_id UUID FK tenants.id
created_by UUID FK users.id
title text
created_at, updated_at timestamptz
```

`DocumentVersion`

```text
id UUID PK
tenant_id UUID FK tenants.id
document_id UUID FK documents.id
version_number integer
upload_session_id UUID unique FK upload_sessions.id
status text check uploaded|ready|failed
object_key text unique
original_filename text
declared_media_type text
detected_media_type text
size_bytes bigint
declared_sha256 char(64)
content_sha256_verified_at nullable timestamptz
transport_checksum_sha256 nullable text
created_by UUID FK users.id
created_at, updated_at timestamptz
unique(document_id, version_number)
```

`UploadSession`

```text
id UUID PK
tenant_id UUID FK tenants.id
actor_id UUID FK users.id
pending_document_id UUID
pending_version_id UUID
status text check initializing|active|completing|completed|aborted|expired|failed
idempotency_key text
request_fingerprint char(64)
object_key text unique
object_store_upload_id text nullable
original_filename text
extension text
declared_media_type text
size_bytes bigint
declared_sha256 char(64)
part_size_bytes bigint
expected_part_count integer
reserved_bytes bigint
expires_at timestamptz
completion_started_at, completed_at, aborted_at nullable timestamptz
document_version_id nullable UUID
last_error_code nullable text
created_at, updated_at timestamptz
unique(tenant_id, idempotency_key)
```

`UploadPart`

```text
id UUID PK
tenant_id UUID FK tenants.id
upload_session_id UUID FK upload_sessions.id on delete cascade
part_number integer
expected_checksum_sha256 text
observed_checksum_sha256 nullable text
etag nullable text
size_bytes nullable bigint
observation_version nullable bigint
observed_at nullable timestamptz
verified_at nullable timestamptz
unique(upload_session_id, part_number)
```

Every business table carries `tenant_id` even when reachable through another tenant
row. M1 enforces it in queries and constraints where practical; PostgreSQL RLS remains
a later hardening step.

### Quota transitions

The tenant row is locked while counters change:

```text
create:   reserved += declared_size
complete: reserved -= declared_size; used += actual_size
abort:    reserved -= declared_size
expire:   reserved -= declared_size
```

State predicates and non-negative check constraints make each transition exactly once.
Idempotent replays return existing state without touching counters.

## State Machines

### Server upload session

```text
initializing -> active -> completing -> completed
       |          |          |
       v          v          v
     failed    aborted     failed/reconciled
                  |
                  v
               expired
```

- `initializing` covers the unavoidable PostgreSQL/S3 saga gap while quota is reserved.
- `active` permits presign, list, resume, abort, and complete initiation.
- `completing` is committed before the external complete call. A retry can therefore
  reconcile a successful S3 completion after API termination.
- `completed`, `aborted`, and `expired` are terminal for client behavior. Cleanup may
  reconcile a stale `completing` row to `completed` when the object is valid.

### Browser upload machine

```text
idle -> hashing -> creating -> uploading -> completing -> completed
                    |             |
                    v             v
                 failed <------ failed
                                  ^
uploading <-> paused              |
    |                             |
    `---------- cancel -> canceled
```

Server state is queried, not copied into an independent global store. The reducer owns
the selected `File`, local progress, active XHR handles, retry counters, and legal UI
commands. TanStack Query owns readiness/session server state.

## File Policy

Creation validates normalized metadata before S3 initiation:

| Extension | Declared media type | Completion evidence |
|---|---|---|
| `.txt` | `text/plain` | bounded UTF-8 samples; no NUL |
| `.pdf` | `application/pdf` | leading `%PDF-` signature |
| `.docx` | Office Open XML media type | ZIP central directory plus required entries |

Filename validation rejects path separators, drive/UNC prefixes, control/NUL
characters, dot-only names, Windows reserved basenames, trailing spaces/dots, and names
over the configured limit. The original safe basename is stored for display only.

DOCX inspection uses a bounded S3 range reader. A strict stdlib binary parser reads the
EOCD tail and central directory through two ranged requests; it never reads local member
data or decompresses entries. Policy checks entry count, total declared uncompressed
bytes, maximum member size, compression ratio, encryption, compression method,
ZIP64 EOCD/size/local-offset sentinels and extra fields, duplicate normalized names,
traversal/absolute paths, and required
`[Content_Types].xml` plus `word/document.xml`. This is an envelope check, not a parser;
M3 enforces streamed decompression limits again.

## Multipart Adapter

Core exposes an async protocol implemented by one long-lived boto3 S3 client. Blocking
network operations run outside the event loop through a bounded thread offload. The
client uses Signature V4, path-style addressing for MinIO, configured connect/read
timeouts, and a bounded connection pool.

Two endpoints are explicit:

- service endpoint: reachable from API/cleanup processes;
- presign endpoint: the host embedded in browser URLs.

The adapter supports:

```text
create_upload
presign_upload_part
list_parts (all pages)
complete_upload
head_object with checksum mode
get_range
abort_upload
delete_object
list_incomplete_uploads (all pages)
close
```

Object metadata includes only random session/version identifiers, declared byte size,
and a non-sensitive contract version. User filename, email, and tenant name are absent.

## Checksum And Integrity Model

1. The Web Worker incrementally calculates the complete file's SHA-256 for later content
   identity and a SHA-256 for each bounded part.
2. The presign request contains the base64 part checksum. The signed `UploadPart`
   request requires `x-amz-checksum-sha256`; MinIO/S3 validates bytes against it.
3. `ListParts` must return the expected checksum/ETag/size. Completion sends the exact
   client-maintained ordered list after comparing it to the server listing.
4. The adapter records the combined checksum exposed by the object store when available
   and verifies it against the deterministic checksum contract used by the pinned
   implementation.
5. Multipart ETag is opaque and never interpreted as a whole-file MD5.
6. The plain whole-file SHA-256 remains `declared_sha256` with no verification timestamp
   in M1. M3 recomputes it during the first full streamed read.

If the pinned MinIO implementation cannot enforce the checksum contract, the real
integration test fails and implementation must pin/configure a compatible release or
revise this design explicitly. It must not silently fall back to trusting a client-only
checksum.

## API Contract

All business payloads use camelCase JSON and one typed error envelope:

```json
{
  "error": {
    "code": "upload_part_mismatch",
    "message": "Uploaded parts do not match the completion request.",
    "requestId": "..."
  }
}
```

Routes:

```text
POST   /api/upload-sessions
GET    /api/upload-sessions/{sessionId}
POST   /api/upload-sessions/{sessionId}/parts/{partNumber}/presign
POST   /api/upload-sessions/{sessionId}/complete
DELETE /api/upload-sessions/{sessionId}
GET    /api/documents
GET    /api/documents/{documentId}
GET    /api/documents/{documentId}/versions
```

Create returns 201 for a new session and a replay marker for an idempotent existing
session. Presign returns URL, expiry, and exact required headers. Get returns current
state and verified uploaded parts. Complete returns the durable document/version IDs.
Delete returns 204 for first and repeated abort, while a completed session returns 409.

Complete accepts the client-maintained ordered list:

```json
{
  "parts": [
    {
      "partNumber": 1,
      "sizeBytes": 5242880,
      "etag": "\"opaque-etag\"",
      "checksumSha256": "base64-sha256"
    }
  ]
}
```

The list is compared byte-for-byte with database expectations and a fresh complete
`ListParts` result before external completion.

The API CORS configuration adds only required methods and headers (`Authorization`,
`Content-Type`, `Idempotency-Key`, request/correlation IDs) and exposes request and
correlation response headers.

## Transaction And Failure Semantics

### Create saga

1. Normalize and validate input; calculate a request fingerprint.
2. In a transaction, lock tenant, enforce quota, insert/replay the `initializing`
   session, and reserve bytes.
3. Initiate multipart outside the transaction.
4. Persist the upload ID and transition to `active`.

A crash between steps 3 and 4 can create an orphan multipart upload. The random object
key includes the session UUID, so the cleanup command can correlate or abort it after a
grace period.

An existing `initializing` replay performs a bounded poll and never returns a successful
`initializing` response. Reservation or activation COMMIT acknowledgement loss is
resolved by rereading PostgreSQL before deciding whether to continue, return success,
or compensate. The service never aborts while the activation result is unknown. Once
PostgreSQL confirms that a newly created upload ID is unclaimed, it is aborted; if that
abort fails, the row becomes `failed`, retains the upload ID, releases quota once, and
remains a durable cleanup target.

`GET` allocates a PostgreSQL sequence version before `ListParts` and records the request
time in `observed_at`. Matching or explicit mismatching observations update a part only
when that version is newer than the stored value, so equal timestamps, wall-clock
rollback, and an older slow request cannot overwrite newer evidence. Explicit mismatch
clears prior verification; absence from one listing does not erase a previously verified
part in the same multipart generation.

### Complete reconciliation

1. Lock and authorize the session. A completed session returns its existing version.
2. Transition `active` to `completing` and commit.
3. List and verify parts, then call S3 complete.
4. Verify object head, metadata, size, checksum contract, and bounded file envelope.
5. In a second transaction, lock session and tenant, insert Document/Version with
   preallocated IDs and unique `upload_session_id`, convert quota, and mark completed.

If step 3 succeeds but the process dies, retry sees `completing`. `NoSuchUpload` is not
treated as proof of failure; the service verifies the exact object key and metadata and
continues step 4. Unique constraints make finalization idempotent under concurrent
retries.

Invalid completed objects are deleted only after identity metadata proves they belong
to the session. The session moves to `failed` and its reservation is released.

## Cleanup

`scripts/cleanup_uploads.py` is an explicit, rerunnable M1 command. It processes small
batches and uses row locks with skip-locked semantics so later scheduling can run more
than one instance safely. It handles:

- expired `initializing`/`active` sessions;
- stale `completing` reconciliation;
- terminal failures with unreleased reservations;
- old object-store multipart uploads with parseable random M1 keys and no live database
  row.

`DELETE /api/upload-sessions/{id}` first locks tenant then session and commits the
business transition before making the remote abort call. `initializing` and `active`
become `aborted`, their reservation is released once, and an existing upload ID is kept
until S3 abort is confirmed. `aborted` is an idempotent replay; `NoSuchUpload` is also a
successful remote terminal state. A remote failure leaves the aborted row and upload ID
as a durable retry target. `completing` conflicts with abort, while `completed` always
conflicts and its object/version are never deleted.

Cleanup claims eligible rows in a short PostgreSQL transaction with
`FOR UPDATE SKIP LOCKED`, a random claim token, and a bounded claim lease. Network calls
run after that transaction. Every terminal write locks tenant then session, rechecks the
claim token and source identity, and clears the claim. A crashed worker is recoverable
after the lease expires; a concurrent API completion or abort clears the claim and wins.

Expired `initializing`/`active` rows transition to `expired` and release quota before
remote abort. `aborted`/`expired`/`failed` rows with a retained upload ID are retryable
cleanup targets. Failed completion rows first attempt multipart abort; when
`NoSuchUpload` proves the multipart is gone, HEAD metadata must prove exact M1 ownership
before an invalid completed object may be deleted. Ambiguous ownership keeps the row and
causes a non-zero cleanup result.

Stale `completing` cleanup uses the same completion reconciler as the API. Durable
verified part observations are compared with a fresh `ListParts`; a present multipart is
completed, while a missing multipart permits exact-key HEAD reconciliation. A valid
owned object uses the existing Document/Version finalization transaction. Multipart and
object both missing produce a durable failed row and one quota release. Unavailable,
protocol-invalid, or ambiguous external state remains retryable and is never converted
into destructive evidence.

The orphan scan accepts only exact `m1/uploads/{32 lowercase hex}/{32 lowercase hex}`
keys with an aware initiation time older than the configured grace period. It rechecks
PostgreSQL immediately before abort and skips any key/session with a live row. Malformed,
young, timestamp-ambiguous, or database-owned uploads are not aborted.

Dry-run performs eligibility and orphan discovery without claims, database mutations,
abort, completion, or delete calls. The command prints one compact JSON summary with
structured counters and exception-class counts only. Any processing error or ambiguous
ownership returns exit code 1; success returns 0 and argparse errors retain exit code 2.

No scheduler is claimed in M1. M2 may invoke this command from durable scheduled work.

## Frontend Design

- `hash.worker.ts` uses an incremental SHA-256 implementation and reads one file slice
  at a time. Worker messages are versioned and typed.
- `transfer.ts` uses XHR for upload progress, required signed headers, ETag access, and
  abort support. A scheduler caps active transfers at four.
- `machine.ts` is a pure reducer with effect commands so transitions can be unit-tested
  without React or real network calls.
- `persistence.ts` stores session ID, safe filename, size, hash, part size, and expiry.
  It rejects unknown schema versions and never stores JWTs, upload IDs, signed URLs, or
  object keys.
- The token is held in session storage for the local development UI. The user must
  reselect the original file after refresh because browsers cannot safely persist an
  arbitrary 1 GiB `File` handle across sessions.

The existing readiness view remains visible as a compact operational band. The upload
workspace is the primary M1 action area and uses unframed sections plus cards only for
repeated part/status rows.

### Implemented Slice 8 browser contracts

The initial browser path uses two bounded passes because creation needs a whole-file
hash before the server returns its selected part size. The first pass hashes the whole
file without buffering it; after create, the second pass recomputes the whole hash while
also producing exact server-sized part checksums. Recovery already has the persisted
part size and therefore performs one pass before fetching server state.

Worker messages use an exact versioned protocol and a per-job Worker. Malformed
responses, Worker construction/start/runtime failures, and unreadable messages settle
and terminate the job. The API boundary uses strict Zod schemas, requires an explicit
HTTP(S) object-store origin allowlist, and binds each presign response to the requested
part number, size, checksum, and checksum header. XHR copies all returned signed headers
without adding bearer authentication and requires an exposed opaque ETag.

The reducer is pure and returns typed effects. Generation and attempt fences reject
late work. Pause clears queued work and aborts browser requests only; cancel also invokes
server abort. A create response arriving after local cancel becomes a compensating
abort. The scheduler caps active transfers at four and queues a newer generation of a
part behind an older aborting generation rather than starting them concurrently.

Recovery persistence is a strict seven-field whitelist plus schema version. Tokens use
a separate session-storage key; signed URLs, headers, object keys, object-store upload
IDs, and file bodies are not persisted. Filename and size are checked before hashing;
the complete hash and server session/part identity are checked before any part is queued.

### Implemented Slice 9 operational UI

`UploadWorkspace` keeps the reducer as the only transition authority. A React controller
holds the current reducer state in a ref, applies one action at a time, and interprets
typed effects into Worker jobs, API calls, persistence writes, scheduler commands, and
XHR handles. The controller does not infer legal transitions from component state.

The local JWT is stored separately in session storage and supplied through a live token
getter. Native and injected fetchers are invoked without binding `UploadApiClient` as
their receiver; real Chromium exposed the otherwise mock-hidden `Illegal invocation`
failure. The controller resumes its scheduler during effect setup so React StrictMode's
development cleanup/setup cycle cannot leave all future part tasks permanently paused;
unmount cleanup still cancels hashing/transfers and pauses queued work.

The primary view is the upload workspace, with readiness retained as a lower operational
section. It provides token, file, pause/resume/retry/cancel, progress, completion IDs, and
per-part controls without persisting or rendering signed URLs, object keys, upload IDs,
or checksums. Responsive layout uses full-width operational bands and cards only for
repeated service/part rows.

Playwright owns a deterministic real-service fixture: Compose starts PostgreSQL, Redis,
and MinIO; Alembic upgrades; a local principal is bootstrapped; API and Vite are managed
web servers. A 17 MiB two-part TXT upload is held at the MinIO PUT boundary, paused,
reloaded, tested with same-name/same-size wrong content, reloaded again, reconciled, and
completed. Screenshots at 1440x900 and 390x844 are accompanied by horizontal overflow
and major-section overlap assertions. This proves browser recovery at moderate size; it
does not replace Slice 10's 1 GiB smoke or memory evidence.

## Observability And Privacy

- Request/correlation IDs remain the cross-request identifiers.
- Successful principal resolution adds `tenant_id`, `actor_id`, and membership role to
  request context; only IDs are safe log/span attributes.
- Domain logs use session/document IDs only where operationally necessary. Filenames,
  token claims beyond IDs, object keys, upload IDs, checksums, signed URLs, and bodies
  are excluded.
- S3 exceptions are mapped to stable error classes/codes. Raw response bodies and
  request URLs are not logged.

## Verification Design

### Fast tests

- pure validation, part planning, state transitions, checksum formatting, JWT parsing;
- typed API/error contracts with injected principal/upload services;
- Web reducer, worker protocol, persistence, scheduler, and response validation.

### Real integration

- PostgreSQL migration and constraints;
- JWT plus persisted membership;
- MinIO multipart checksum/CORS/list/complete/abort;
- create/complete concurrency, crash reconciliation, tenant isolation, quota, cleanup;
- a small CI integration upload and a separate local 1 GiB evidence run.

The generated smoke keeps one server-selected part in client memory and sends it to the
object store. It stops and restarts the API between the leading and remaining parts,
then reconstructs completion input from the reconciled server projection. API RSS is
sampled from the actual listening process, not merely its launcher, and only aggregate
measurements enter the sanitized report.

### Browser evidence

Playwright runs an authenticated upload with a moderate generated fixture, pauses,
reloads, reselects the file, resumes missing parts, completes, and captures desktop and
mobile screenshots. The 1 GiB path is exercised by the streaming smoke client so the
browser test remains deterministic and reviewable.

## Compatibility, Rollout, And Rollback

- Add a new Alembic revision; never edit M0's applied revision.
- Pin the MinIO server/client image versions used by evidence instead of retaining
  mutable `latest` tags once compatibility is established.
- Add settings with backward-compatible local defaults and update `.env.example`.
- M1 rollback may stop API/Web changes and abort test uploads, but it never deletes
  shared named volumes automatically.
- Before M2 starts, M1's tables and completion service are stable. M2 extends the final
  database transaction to add `Job`/`OutboxEvent` rather than creating work after commit.

## Known Risks

- S3-compatible checksum behavior differs by server version. The MinIO integration test
  is a release gate, not a mocked assumption.
- Multipart completion is an external/database saga until M2 adds the ingestion job;
  reconciliation and unique constraints are therefore mandatory.
- Browser refresh cannot retain an arbitrary local `File`; explicit reselection and hash
  matching are part of the product contract.
- A local 1 GiB run consumes real disk and network resources. The smoke checks free disk,
  supports a smaller diagnostic size, and records the actual size used.
