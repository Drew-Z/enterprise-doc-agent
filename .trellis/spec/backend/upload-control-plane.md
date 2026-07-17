# Multipart Upload Control Plane

## Scenario: Slice 5 create, presign, and resume

### 1. Scope / Trigger

Use this contract when changing authenticated multipart session creation, part presign,
or `ListParts` reconciliation across Core, API, PostgreSQL, and S3-compatible storage.
PostgreSQL owns business state; the object store owns multipart bytes and observations.

### 2. Signatures

- `POST /api/upload-sessions`
- `GET /api/upload-sessions/{session_id}`
- `POST /api/upload-sessions/{session_id}/parts/{part_number}/presign`
- `MultipartObjectStore.presign_upload_part(..., expires_in_seconds: int)`
- `upload_parts.observation_version bigint NULL` strictly orders remote observations;
  `observed_at timestamptz NULL` records their request time.

### 3. Contracts

- Business requests contain exactly one `Authorization: Bearer <token>` header.
- Create reserves quota once and never returns an `initializing` session as a successful
  replay. A replay waits briefly for activation, then returns active/terminal state or
  typed `upload_initialization_in_progress`.
- A part has one immutable expected base64 SHA-256 value per upload generation.
- Presign TTL is `min(configured_ttl, floor(session_expires_at - now))`; less than one
  remaining second is expired.
- A listed checksum/size mismatch clears prior verification. Absence from one complete
  listing is not destructive evidence because a part cannot be deleted independently
  inside the same multipart generation.
- Normal logs use stable event names and structured primitive fields. Dynamic message
  arguments, signed URLs, upload IDs, object keys, filenames, hashes, and arbitrary
  exception strings are not rendered.

### 4. Validation & Error Matrix

- Missing bearer header -> `401 auth_missing`.
- Duplicate/malformed bearer header -> `401 auth_invalid`.
- Same idempotency key with different actor/fingerprint -> `409 upload_idempotency_conflict`.
- Existing initialization exceeds bounded wait -> `503 upload_initialization_in_progress`.
- Invalid part number/size/checksum -> `400` typed part error.
- Different checksum for an existing expectation -> `409 upload_part_checksum_conflict`.
- Expired session or remaining presign TTL below one second -> `410 upload_session_expired`.
- Object-store unavailable -> `503`; protocol violation -> `502`.

### 5. Good/Base/Bad Cases

- Good: activation COMMIT succeeds but its acknowledgement is lost; a fresh read sees
  the same active upload ID, so the request succeeds without aborting it.
- Base: two identical creates share one reservation; the replay waits and returns the
  same active session.
- Bad: aborting an unactivated multipart fails. Preserve the upload ID in a `failed`
  session, release quota once, and leave cleanup a durable retry target; do not delete
  the only association.

### 6. Tests Required

- Fault injection after reservation and activation COMMIT; assert one row, one quota
  reservation, one multipart creation, and no abort of a committed active upload.
- Blocking initiate tests for replay wait, timeout, owner failure, and concurrent expiry.
- Concurrent GET tests where an older remote result returns after a newer result, plus
  repeated wall-clock timestamps; assert the database sequence CAS prevents stale writes.
- Real MinIO tests for checksum-bound PUT, session-bounded TTL, GET expiry, immutable
  expectation uniqueness, malformed checksum mapping, and owner boundaries.
- OpenAPI tests for BearerAuth and every declared typed error response.

### 7. Wrong vs Correct

#### Wrong

```python
try:
    await commit_activation()
except Exception:
    await object_store.abort_upload(...)
```

This can destroy an upload whose database COMMIT succeeded but whose acknowledgement
was lost.

#### Correct

```python
try:
    await commit_activation()
except Exception:
    current = await reread_session()
    if current.is_active_with(upload_id):
        return current
    await abort_only_the_unclaimed_upload()
```

Remote observations follow the same ordering rule: allocate a database sequence version
before `ListParts`, then apply changes only when that version is newer than the stored one.
`observed_at` remains audit metadata and is never used as the ordering authority.

## Proven Examples

- `packages/core/src/enterprise_doc_core/uploads/service.py`
- `packages/core/src/enterprise_doc_core/uploads/session_service.py`
- `apps/api/src/enterprise_doc_api/uploads/router.py`
- `tests/multipart/test_upload_create_integration.py`
- `tests/multipart/test_upload_reconciliation_integration.py`
- `tests/multipart/test_upload_resume_integration.py`
