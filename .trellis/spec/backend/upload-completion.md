# Multipart Upload Completion

## Scenario: bounded authenticated content upload

### 1. Scope / Trigger

Small files received by the API use the trusted Core writer below. This is a local
server capability; browser integration and public performance remain unverified.

### 2. Signatures

`POST /api/upload-sessions/content` with `Idempotency-Key` calls existing creation
and `UploadSessionService.complete_content(..., content: bytes, writer)`.

### 3. Contracts

JSON fields: `filename`, `mediaType`, positive integer `sizeBytes` <=1MiB,
lowercase hex `sha256`, and `contentBase64`. No client receipts or transport fields.
`UPLOAD__SINGLE_PUT_ENABLED` gates the route before reading its body. Stream-count
the wire bytes before JSON parsing, including when Content-Length is absent or
false; cap at 1,414,488 bytes (base64 expansion plus 16KiB metadata allowance).
Authenticate before body read; use the existing owned-session and quota contracts.

Return `{session, completion}` only after service finalization, with no-store.
First creation is 201, same-key replay 200. If an old same-key session is multipart,
return that session and `completion: null`; never reinterpret its persisted transport.
The response omits optional default fields such as `initialUpload: null`.

### 4. Validation & Error Matrix

| Condition | Result |
| --- | --- |
| Disabled / unauthenticated | 404 upload_content_unsupported / 401 before body read |
| Missing key / wrong content type | 400 / 415 before mutation |
| Oversize wire body | 413 before JSON parse, even with false Content-Length |
| Invalid schema/base64 or extra fields | 422 without content in errors |
| Decoded size/SHA mismatch | 400 upload_content_mismatch before creation |
| Owned completion/store failure | Existing typed error; retain same-key recovery |

### 5. Good/Base/Bad Cases

Good: immutable verified bytes, signed create-only PUT, one durable finalization.
Base: old multipart same-key session continues its existing protocol.
Bad: lost object/HTTP response triggers a new idempotency key and duplicates quota.

### 6. Tests Required

`apps/api/tests/test_upload_content_bounds.py` proves authentication, capability,
stream bounds, invalid content and strict fields through ASGI. Real PostgreSQL tests
in `tests/multipart/test_upload_content_http_integration.py` inject only identity and
external object/HTTP boundaries. Normal, concurrent, before-write failure, lost PUT
acknowledgment and lost HTTP response must converge to one session/document/version,
one Job/Outbox and one storage conversion. Normal success performs no readback;
ambiguous writes must read back. No production DB is a test fixture.

### 7. Wrong vs Correct

Wrong: `payload = await request.json()` followed by a size check.
Correct: count `request.stream()` chunks against the wire limit before
`ContentUploadRequest.model_validate_json(...)`; then verify decoded size and SHA256.

## Scenario: Slice 6 completion and crash reconciliation

### 1. Scope / Trigger

Use this contract when changing the transition from an authenticated multipart upload
session into one durable uploaded `DocumentVersion`. PostgreSQL owns session state,
identity, quota, and document rows. S3-compatible storage owns multipart bytes and the
completed object. M1 does not create ingestion jobs or outbox events.

### 2. Signatures

- `POST /api/upload-sessions/{session_id}/complete`
- `UploadSessionService.complete(principal, session_id, request)`
- `MultipartObjectStore.read_object(bucket, key, max_bytes) -> ObjectContent`
- `validate_document_envelope(object_store, bucket, key, size_bytes, extension, settings)`
- `upload_sessions.document_version_id -> document_versions.id` is nullable and unique.

The request contains an ordered `parts` array. Every item contains `partNumber`,
`sizeBytes`, `etag`, and canonical base64 `checksumSha256`.

### 3. Contracts

- The client part list, database checksum expectations, and a fresh complete `ListParts`
  result must have exactly the planned consecutive sequence `1..N` and identical size,
  ETag, and checksum values.
- Missing, extra, duplicate, reordered, or mismatching parts fail before
  `CompleteMultipartUpload`; an active session remains resumable.
- The service commits `active -> completing` before calling the external completion API.
- `NoSuchUpload` is not success by itself. Only a session already confirmed as
  `completing` may reconcile the exact random object key through `HeadObject`.
- If a refreshed session is still `active` after `ListParts` returns `NoSuchUpload`, the
  multipart generation is terminally missing: lock tenant then session, verify the
  server-owned upload identity, release its reservation once, persist `failed`, and
  return the stable object-store error without attempting HEAD reconciliation.
- The completed head must match declared size and server-owned metadata: contract,
  upload session ID, pending version ID, and declared size. Completion and head ETag and
  transport checksum must agree when the completion response is available.
- PDF reads only the first five signature bytes. TXT reads either the whole small file
  or bounded head/tail samples and rejects sampled NUL or invalid UTF-8. DOCX reads only
  the bounded EOCD tail and central directory; it does not decompress members.
- DOCX validation rejects multi-disk/ZIP64 envelopes, malformed central records,
  encryption, unsupported compression methods, unsafe or duplicate normalized paths,
  excessive entry/size/ratio declarations, and missing Office entries.
- ZIP64 rejection includes EOCD sentinels, central-directory size fields, local-header
  offset sentinels, and ZIP64 extra field `0x0001`; member data is never read.
- Finalization locks tenant then session, inserts the preallocated Document and Version,
  converts reserved bytes to used bytes, sets the unique reverse version link, and marks
  the session completed in one PostgreSQL transaction.
- In native-checksum mode, at the end of upload completion, `declared_sha256` remains unverified and
  `content_sha256_verified_at` remains null. The object-store transport checksum is
  stored separately. Downstream document ingestion verifies the complete spooled
  bytes before parsing, records the marker with the chunk checkpoint and requires
  it for activation. This later Worker contract does not change completion's
  bounded envelope checks; see [presales ingestion](../foundation-tests/backend/presales-ingestion.md).
- Readback-checksum mode verifies every expected part and the full declared SHA256
  before recording `content_sha256_verified_at`. For objects at most 1 MiB, retain
  metadata and bounded bytes from one complete `GetObject` response, avoiding a separate
  HEAD and per-part GETs. `ObjectContent` contains its `head` and `content`; an object
  larger than `max_bytes` returns metadata with `content=None` without reading its body,
  allowing the existing ownership/size rejection and identity-gated cleanup to apply.
  The adapter closes the body on every available exit path and rejects partial responses,
  invalid metadata, truncation or excess bytes. Blocking I/O stays in the worker thread.
  Verify every recorded part boundary and the whole SHA256 in memory before running the
  existing envelope validators. The buffer must match the full object
  size, remain scoped to its bucket/key and never survive the completion call.
  Large files and native-checksum mode keep their bounded remote envelope reads.
  Reuse does not skip signature, UTF-8, ZIP metadata, quota or finalization checks.
- Completed replay and final COMMIT acknowledgement loss reread the same durable version
  without calling object-store completion or changing quota again.
  Validate immutable tenant/session/version/document links, not the version's mutable
  status: ingestion keeps it `uploaded` while processing, then advances it to `ready`
  or `failed`. All three states retain the same successful upload receipt.
- Invalid completed objects are deleted only after server metadata proves ownership.
  The failed session releases its reservation once and remains visible to cleanup if
  deletion fails or ownership is ambiguous.
- If the failure transaction COMMIT acknowledgement is lost, reread PostgreSQL and
  continue idempotent deletion only when the expected `failed` state, zero reservation,
  absent version link, error code, and upload identity are all durable.

### 4. Validation & Error Matrix

- Missing/reordered/duplicate/client-conflicting parts -> `409 upload_completion_parts_invalid`.
- Fresh S3 list/head mismatch -> `409 upload_completion_verification_failed`.
- Expired active session -> `410 upload_session_expired`.
- Session outside tenant/actor boundary -> `404 upload_session_not_found`.
- Invalid PDF/TXT/DOCX envelope -> `409` with a stable `document_*` code.
- Missing multipart/object during an unreconciled state -> `409` object-store error.
- Object-store unavailable -> `503`; protocol violation -> `502`.
- Oversize small-object response -> read no body; preserve the existing size/ownership
  rejection, one-time reservation release and owned-object-only deletion.
- Broken completed/link invariant or unrecovered finalization -> typed `500`.
- A completed session linked to a ready/failed version -> `200`, same IDs and
  completedAt, `replayed=true`; this is not `upload_completion_state_invalid`.

### 5. Good/Base/Bad Cases

- Good: S3 completion succeeds and the process stops before database finalization. The
  retry sees `completing`, receives `NoSuchUpload`, verifies HEAD metadata and envelope,
  and finalizes the preallocated version once.
- Base: two concurrent complete calls race. One finalizes; the other either reconciles
  HEAD or sees the durable link and returns the same version with `replayed=true`.
- Bad: treating `NoSuchUpload` as proof that the object completed could attach an
  unrelated or absent object. HEAD identity, size, checksum, and envelope are mandatory.

### 6. Tests Required

- Unit tests for bounded PDF/TXT/DOCX reads and every envelope policy category.
- API contract tests for strict ordered part fields, typed errors, BearerAuth, and a
  response without object-store identifiers. Unknown top-level and nested fields are
  rejected instead of ignored.
- Real PostgreSQL/MinIO concurrent completion proving one Document, one Version, and one
  quota conversion.
- Fault injection after S3 completion and after finalization COMMIT, proving retry and
  acknowledgement recovery return the same pending version ID.
- Invalid completed object tests proving identity-gated deletion and exactly-once quota
  release.
- Advance the linked version to ready/failed in PostgreSQL, replay complete and assert
  unchanged document/version/completion time, used/reserved bytes, version count and
  object-store completion-call count.
- Verify small readback completions avoid duplicate GETs, large files retain remote
  envelope reads, and completed replay does not re-read or consume storage twice.
  Invalid buffered PDF/TXT/DOCX content must retain the same stable rejection codes.
- SDK boundary tests assert one full GET with same-response metadata/bytes, bounded read,
  no body read for oversize, and closure after malformed metadata, partial or truncated data.
- Real PostgreSQL/MinIO public API concurrency covers both checksum modes; direct completion
  and stale-cleanup recovery after object completion still finalize once. Small-object
  foreign metadata and both size mismatches release the reservation and delete only when owned.

### 7. Wrong vs Correct

Wrong: reject a completed receipt when `version.status != "uploaded"`.
Correct: verify the immutable completion links; downstream ingestion status does not
invalidate the upload receipt or authorize a second quota conversion.

Wrong: treat the combined GET buffer as already verified because transport succeeded.
Correct: bind its metadata to the owned upload and verify all part/full hashes before
format validation and finalization; the optimization removes a round trip, not a check.

Completion-only reads (`_load_completion_state`, `_load_stale_completion_state`,
and `_read_completed_result`) use `read_only_session`: PostgreSQL prevents writes,
successful exit commits, and errors still roll back. This preserves psycopg's
prepared-statement cache across repeated reads. Claim/finalization transactions
keep their row locks and existing commit semantics. Do not mechanically convert
every apparent read: upload `get()` allocates an observation sequence value and
therefore cannot run in a PostgreSQL read-only transaction.

The regression test inspects actual `pg_prepared_statements` after eight completed
replays, checks identical receipts, one object completion and one quota conversion,
and confirms the returned pool connection is writable. Commit-loss injection
targets the numbered writable commit rather than counting intervening successful
read transactions. Cache retention and short read latency do not prove the whole
upload's public p95 or the two capacity rounds.

An empty-parts completion uses a single owned-session `FOR UPDATE` transaction to
load and claim a direct (`single_put`) upload. It validates status, expiry, transport
and bounded size before committing `completing`; invalid requests roll back without
changing reservations or contacting object storage. Unfinished multipart uploads
still require their complete part list. A completed receipt is replayable with an
empty list for either transport, retaining the existing completion contract.
The claim commits and releases its lock before object reads. Multipart calls with
parts retain their read-only preflight, fresh part verification and snapshot identity
checks. Finalization still locks tenant then session and converts quota exactly once.
Real PostgreSQL tests must prove one owned-session SELECT before direct object I/O,
durable `completing` and immediate lock acquisition by a separate transaction at that
boundary, plus unchanged rejection, concurrent completion and crash recovery behavior.

### Trusted server-received small content (Core capability)

`UploadSessionService.complete_content(principal, session_id, content, writer)`
accepts immutable bytes within the existing 1 MiB limit. Its claim transaction
verifies the owned session's transport, declared size and SHA256 before changing
state, including on replay; mismatching input cannot poison a valid in-flight
completion. The existing envelope validator receives those verified full bytes.

`Boto3SignedUploadWriter.write_content` owns a separate S3 client with explicit
SigV4 payload signing. It sends the full SHA256 and `If-None-Match: *`, validates
HTTP 200 plus a nonempty ETag, and returns a receipt for its own write. It never
represents that digest as a stored native checksum or accepts client receipts.
Other object-store clients keep their existing signing defaults. The real R2
negative probe rejected a body changed after signing without creating an object;
this is request-payload integrity, distinct from R2's HEAD checksum support.

Only that acknowledged fresh write can use its verified bytes/owned metadata
without a second object read. Any object-store error or mismatched write receipt
falls back to the original owned-object readback path. A missing object remains
in `completing` for recovery rather than being treated as success. Cancellation
does not manufacture a receipt. Finalization retains the existing tenant→session
lock order, preallocated IDs, job/outbox and one-time quota conversion.

Validation must use real SDK signing with only the HTTP boundary replaced, plus
isolated PostgreSQL for no-lock-during-PUT, concurrent completion/replay, lost
response recovery and invalid actor/tenant/hash/size/expiry/envelope rejection.
The Core capability alone is not a bounded HTTP ingress, browser recovery or
public performance result; those integrations require their own checks.

#### Wrong

```python
try:
    await object_store.complete_upload(...)
except MultipartUploadNotFound:
    mark_session_failed()
```

The multipart upload disappears after successful completion, so this loses a valid
object when the API process stopped before PostgreSQL finalization.

#### Correct

```python
mark_completing_and_commit()
try:
    completed = await object_store.complete_upload(...)
except MultipartUploadNotFound:
    completed = None
head = await object_store.head_object(...)
verify_identity_size_checksum_and_envelope(head, completed)
finalize_preallocated_version_and_quota_once()
```

## Proven Examples

- `packages/core/src/enterprise_doc_core/uploads/session_service.py`
- `packages/core/src/enterprise_doc_core/documents/envelope.py`
- `apps/api/src/enterprise_doc_api/uploads/router.py`
- `packages/core/tests/test_document_envelope.py`
- `tests/multipart/test_upload_complete_integration.py`
- `packages/core/src/enterprise_doc_core/object_store/signed_upload.py`
- `packages/core/tests/test_signed_object_upload.py`
- `tests/multipart/test_signed_upload_completion_integration.py`
