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

### MinIO browser CORS

Fetched source:

- https://docs.min.io/aistor/administration/cors-configuration
- Command used:
  `smart-search fetch "https://docs.min.io/aistor/administration/cors-configuration" --format markdown`

Relevant verified facts:

- MinIO supports global and per-bucket S3 CORS configuration.
- Per-bucket rules take precedence.
- Browser upload rules can allow PUT/HEAD and expose ETag.

Decision: configure per-bucket CORS for exact local Web origins, required methods and
headers, and exposed ETag/checksum headers. Do not rely on wildcard global CORS.

### Incomplete multipart cleanup

Fetched source:

- https://docs.min.io/aistor/administration/object-lifecycle-management/lifecycle-rule-patterns
- Command used:
  `smart-search fetch "https://docs.min.io/aistor/administration/object-lifecycle-management/lifecycle-rule-patterns" --format markdown`

Relevant verified fact: incomplete multipart uploads consume storage and require an
abort/lifecycle policy. Some simple expiry rules may also affect completed objects.

Decision: M1 implements an ownership-aware database/object-store cleanup command first.
It does not add a broad bucket expiration rule that could delete completed documents.

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

## Research Limitations

- The broad Smart Search synthesis route returned an empty provider result for one AWS
  query. The design therefore relies on directly fetched AWS and MinIO documentation,
  plus a mandatory real MinIO integration probe.
- MinIO server behavior can vary by release. The image pin is selected only after the
  integration probe passes and is then recorded in evidence.
