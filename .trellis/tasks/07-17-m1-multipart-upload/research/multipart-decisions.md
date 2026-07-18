# M1 Multipart Research And Locked Decisions

Verified on 2026-07-17 against the M0 repository and fetched vendor documentation.

## Repository Facts

- `packages/core/src/enterprise_doc_core/context/request.py` already defines optional
  `PrincipalContext`, but `apps/api/src/enterprise_doc_api/middleware/request_context.py`
  creates requests without a principal. No tenant-scoped endpoint may be added until a
  resolver enriches this context.
- `packages/core/src/enterprise_doc_core/db/engine.py` creates an async SQLAlchemy engine
  but no declarative metadata or session factory exists. M1 must add both and make
  Alembic import the same metadata.
- `packages/core/src/enterprise_doc_core/db/migrations/env.py` currently uses empty
  `MetaData`; M0 revision 0001 enables only `vector` and must remain immutable.
- `packages/core/pyproject.toml` already includes boto3, SQLAlchemy async, psycopg, and
  Alembic. JWT verification and browser incremental hashing are not yet dependencies.
- `apps/api/src/enterprise_doc_api/app.py` currently allows only GET CORS methods and
  request/correlation headers. M1 must explicitly add business methods/auth headers.
- `infra/compose/docker-compose.yml` uses mutable MinIO `latest` images and does not set
  bucket CORS. M1 evidence requires a tested pin and browser-visible ETag/checksum
  headers.
- `apps/web/src/App.tsx` is a single readiness view. TanStack Query owns server state;
  no global client store exists. A feature-local reducer is therefore the smallest
  compatible upload state mechanism.

## Vendor Documentation Evidence

### Amazon S3 multipart upload

Fetched source:

- https://docs.aws.amazon.com/AmazonS3/latest/userguide/mpuoverview.html
- Command used:
  `smart-search fetch "https://docs.aws.amazon.com/AmazonS3/latest/userguide/mpuoverview.html" --format markdown`

Relevant verified facts:

- Multipart upload is initiate, upload parts, then complete.
- Part numbers are 1 through 10,000; reusing a part number overwrites that part.
- The client must retain each part number and ETag for completion.
- The completed object ETag is not necessarily an MD5 of the object.
- Incomplete uploads do not expire automatically; they must be completed or aborted.
- ListParts is paginated at 1,000 results and is for verification, not a substitute for
  the client-maintained completion list.
- Checksum-enabled multipart completion requires consecutive part numbers starting at 1.
- S3 can validate additional checksums and returns `BadDigest` on mismatch.

Decision: M1 requires consecutive parts, preserves the client list, verifies it against
all ListParts pages, and treats ETag as opaque.

### Amazon S3 presigned URL lifetime

Fetched source:

- https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html
- Command used:
  `smart-search fetch "https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html" --format markdown --output C:\tmp\smart-search-evidence\slice5-presigned-url-lifecycle.md`

Relevant verified facts:

- Presigned URLs are bearer tokens and can be used multiple times until their expiry or
  until the signing credentials become invalid.
- SigV4 presigned uploads can bind SHA-256 through the required checksum header.
- S3 evaluates expiry when the HTTP request begins, so a database expectation change
  cannot revoke an already issued URL.

Decision: Slice 5 records one immutable checksum expectation per session part. Repeated
presign uses the same checksum; a different checksum returns a typed conflict. Safe
replacement requires aborting the multipart upload and creating a new upload generation
so old URLs reference an invalid upload ID. M1 does not claim a race-prone in-place
replacement.

### ListParts observation ordering

`ListParts` is called outside a database transaction, so concurrent GET requests can
return in a different order than they started. Slice 5 allocates a PostgreSQL sequence
version before the remote call and applies matching or explicit mismatching observations
only when that version is newer than the stored value. `observed_at` remains audit
metadata rather than an ordering authority, so equal timestamps or wall-clock rollback
cannot suppress a newer request. A listed mismatch clears verification.

Absence is not treated as destructive evidence in the same multipart generation: S3
does not expose a delete-part operation, while compatible stores may briefly return an
incomplete observation. Session status and upload ID are rechecked after the remote
call; abort or generation change prevents any stale write.

### Completion reconciliation anchor

Completion commits `active -> completing` before the external complete call. A retry is
therefore allowed to interpret `NoSuchUpload` only after PostgreSQL confirms the same
session is already completing. It then verifies the exact random object key through
HEAD metadata containing the session ID, pending version ID, declared size, and M1
contract marker. `NoSuchUpload` without this state and identity proof remains an error.
If PostgreSQL still says `active`, the missing multipart is terminal rather than
reconcilable. The service locks tenant then session, rechecks the upload identity,
releases the reservation exactly once, persists `failed`, and returns the stable
`multipart_upload_not_found` error.

The final transaction inserts the preallocated Document and DocumentVersion IDs, stores
the object-store transport checksum separately from the unverified declared whole-file
hash, converts tenant reserved bytes to used bytes, and sets a unique reverse
`document_version_id` link. The link and `DocumentVersion.upload_session_id` form the
replay anchors for concurrent requests and COMMIT acknowledgement loss.
Invalid-envelope failure uses the same acknowledgement rule: after an uncertain COMMIT,
the service rereads the failed row and deletes the owned object only when the error code,
zero reservation, absent version link, and upload identity were durably committed.

### Bounded DOCX envelope

Slice 6 does not use `zipfile` against a network-backed seek abstraction. It parses only
the fixed EOCD and bounded central-directory records already fetched with ranged reads.
This makes the byte budget explicit and avoids accidental local-header/member reads.
ZIP64 and multi-disk envelopes are rejected in M1; members are never decompressed.
ZIP64 checks cover EOCD and size sentinels, the central record's local-header offset, and
the `0x0001` extra field. Extra fields are parsed as bounded length-prefixed records so a
truncated field cannot bypass the envelope policy.
M3 repeats streamed decompression limits while parsing document content.

### MinIO browser CORS

Fetched source:

- https://docs.min.io/aistor/administration/cors-configuration
- Command used:
  `smart-search fetch "https://docs.min.io/aistor/administration/cors-configuration" --format markdown`

Relevant verified facts:

- MinIO AIStor supports global and per-bucket S3 CORS configuration.
- The pinned open-source MinIO server returns `NotImplemented` for `PutBucketCors` and
  `mc cors set`; this was reproduced against `RELEASE.2025-09-07T16-13-09Z`.
- Open-source MinIO supports an exact server-level origin list through
  `MINIO_API_CORS_ALLOW_ORIGIN`.
- Browser-visible methods and exposed ETag/checksum headers must be verified from real
  OPTIONS and UploadPart responses because the community build cannot persist a bucket
  CORS document.
- The pinned community server exposes `ETag` explicitly and checksum headers through
  `X-Amz*`/`*`; the probe also verifies the concrete
  `x-amz-checksum-sha256` response header before relying on that exposure rule.

Decision: the local open-source MinIO profile uses a server-level exact origin list and
never the wildcard default. The real feature probe must prove allowed-origin preflight,
evil-origin rejection, and browser-visible ETag/checksum headers. Production S3 or
AIStor deployments should use per-bucket rules; the community limitation is explicit
rather than represented as implemented.

Additional fetched/reproduced evidence:

- https://github.com/minio/minio/issues/15874
- `smart-search fetch "https://github.com/minio/minio/issues/15874" --format markdown`
- `mc cors set local/documents ...` and boto3 `put_bucket_cors` both returned
  `NotImplemented` on the pinned community image.

### Incomplete multipart cleanup

Fetched source:

- https://docs.min.io/aistor/administration/object-lifecycle-management/lifecycle-rule-patterns
- Command used:
  `smart-search fetch "https://docs.min.io/aistor/administration/object-lifecycle-management/lifecycle-rule-patterns" --format markdown`

Relevant verified fact: incomplete multipart uploads consume storage and require an
abort/lifecycle policy. Some simple expiry rules may also affect completed objects.

Decision: M1 implements an ownership-aware database/object-store cleanup command first.
It does not add a broad bucket expiration rule that could delete completed documents.

Slice 7 uses a short PostgreSQL claim lease rather than holding a row lock across S3
calls. Eligible rows are selected with `FOR UPDATE SKIP LOCKED`, marked with a random
claim token, and revalidated under tenant-then-session locking before every terminal
write. `aborted`, `expired`, or `failed` plus a retained upload ID is a durable external
cleanup target; `NoSuchUpload` is idempotent success. Stale completion delegates to the
Slice 6 reconciler, and ambiguous HEAD ownership is never deletion authority.

## Locked Design Decisions

1. M1 uses signed JWT plus a PostgreSQL membership lookup. Static headers or fabricated
   tenant IDs are rejected.
2. File bytes go directly from Web/smoke client to MinIO/S3. FastAPI handles metadata,
   S3 control calls, and bounded range inspection only.
3. Object keys are random and do not embed user or tenant names.
4. Per-part SHA-256 is mandatory and object-store validated. The declared whole-file
   SHA-256 is not marked verified until M3 reads the complete object.
5. Completion is a recoverable saga with `completing` state and exact-key reconciliation.
6. M1 creates only Document/DocumentVersion. M2 extends the final transaction with
   Job/Outbox and owns the joint atomicity gate.
7. Browser refresh recovery requires reselecting the original file and matching its
   metadata/hash; the application does not persist a 1 GiB Blob or signed URLs.
8. The real MinIO feature probe decides compatibility. No mock-only checksum claim is
   acceptable.
9. Create-saga compensation rereads PostgreSQL after uncertain COMMIT results. It never
   aborts a potentially committed active upload, and an abort failure preserves a
   `failed` row with the upload ID for cleanup instead of deleting the association.
10. Business authentication accepts exactly one Authorization header. Duplicate values
    are rejected before token resolution even when one value is otherwise valid.

### Browser hashing and recovery

Slice 8 uses `hash-wasm` rather than a whole-file `crypto.subtle.digest` call. Reads are
bounded to at most 4 MiB and the hash runner accepts an injected slice reader so chunk,
part, progress, cancellation, and read-failure behavior is deterministic under Vitest.

The server-selected part size creates an unavoidable sequencing constraint: the first
pass calculates the whole-file hash required by create; the second initial-upload pass
uses the returned part size and rechecks the whole hash while calculating part
checksums. A resumed session already persists its part size, so reselection needs only
one pass before server reconciliation.

The browser accepts presigned URLs only from an explicit exact HTTP(S) origin allowlist.
The presign response must echo part number, size, and checksum and expose one matching
checksum request header. Other signed headers remain opaque and are copied exactly.
Bearer authentication is reserved for API control-plane calls and is never added to the
object-store PUT.

Pause and cancel are deliberately different commands. Pause invalidates the current
generation, clears queued browser work, and aborts XHRs without changing server state.
Cancel additionally calls DELETE. A create success arriving after cancel is not ignored;
its session ID is used for a compensating abort so quota cleanup is not deferred solely
to expiration.

### Operational browser integration

Slice 9 keeps one reducer-owned state machine and adds a React effect interpreter rather
than mirroring phases in component-local workflow flags. The controller owns only live
runtime handles: the current Worker job, active XHR abort handles, the scheduler, storage
adapters, and an API port. Async outcomes re-enter the same reducer as typed actions.

Real Chromium established two integration decisions that jsdom mocks did not expose.
First, an extracted native fetch must be called as a plain function; invoking it through
the API-client instance can bind an invalid receiver and fail before a request exists.
Second, effect setup explicitly resumes the scheduler because React StrictMode performs
a development cleanup/setup cycle; cleanup still pauses and aborts work for a real
unmount.

The browser acceptance fixture uses real API/PostgreSQL/MinIO control and data planes.
Only the timing of initial object-store PUT continuation is held so pause is
deterministic. Reload recovery, same-name/same-size wrong-content rejection, fresh
presigns for missing parts, completed object validation, and database finalization all
remain real. The fixture is moderate-size evidence and is not used to claim 1 GiB memory
behavior.

## Pinned Community MinIO Observations

- `ListMultipartUploads(MaxUploads=1)` returned both test uploads with
  `IsTruncated=false`; this release did not honor the requested small page size. The
  adapter still implements and unit-tests the required two-marker pagination loop, and
  the real probe verifies complete aggregation of the server result.
- A directory-style `Prefix` returned no uploads even when matching incomplete uploads
  existed; an exact full object key did match. The adapter first issues the standard
  prefixed request, then falls back on an empty result to fully paginating without a
  prefix and filtering keys client-side. This preserves cleanup correctness for the
  pinned community image, but the fallback is not evidence of production-scale listing
  performance; production S3 or AIStor must use the native prefix path.

## Research Limitations

- The broad Smart Search synthesis route returned an empty provider result for one AWS
  query. The design therefore relies on directly fetched AWS and MinIO documentation,
  plus a mandatory real MinIO integration probe.
- MinIO server behavior can vary by release. The image pin is selected only after the
  integration probe passes and is then recorded in evidence.
