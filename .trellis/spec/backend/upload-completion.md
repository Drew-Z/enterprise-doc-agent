# Multipart Upload Completion

## Scenario: Slice 6 completion and crash reconciliation

### 1. Scope / Trigger

Use this contract when changing the transition from an authenticated multipart upload
session into one durable uploaded `DocumentVersion`. PostgreSQL owns session state,
identity, quota, and document rows. S3-compatible storage owns multipart bytes and the
completed object. M1 does not create ingestion jobs or outbox events.

### 2. Signatures

- `POST /api/upload-sessions/{session_id}/complete`
- `UploadSessionService.complete(principal, session_id, request)`
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
- `declared_sha256` remains unverified and `content_sha256_verified_at` remains null.
  The object-store transport checksum is stored separately.
- Completed replay and final COMMIT acknowledgement loss reread the same durable version
  without calling object-store completion or changing quota again.
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
- Broken completed/link invariant or unrecovered finalization -> typed `500`.

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

### 7. Wrong vs Correct

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
