# M1 Multipart Upload: TDD Implementation Plan

## Preconditions

- M0 is archived and fast-forwarded into `main` at commit `360168f`.
- The active implementation branch is `feat/m1-multipart-upload`.
- The parent task remains the integration owner and is not the implementation target.
- M1 owns uploaded document versions only. Do not introduce M2 job/outbox/worker-runtime
  behavior.
- Do not mark a checksum, 1 GiB upload, memory characteristic, or browser recovery as
  passing until its real command output is captured.

## TDD Slice Rules

For every behavior slice:

1. Write one focused failing test or executable contract and confirm it fails for the
   intended missing behavior.
2. Add the smallest implementation that makes the focused test pass.
3. Run the focused test and affected package gates.
4. Refactor only while green, then rerun the same gates.
5. Record material runtime evidence only after the reviewed implementation commit exists.
6. Stop at the slice rollback point rather than crossing into M2-M7 scope.

## Activation

After the planning artifacts and context manifests validate, start the task:

```powershell
uv run python .trellis/scripts/task.py validate 07-17-m1-multipart-upload
uv run python .trellis/scripts/task.py start 07-17-m1-multipart-upload
git branch --show-current
```

Expected branch: `feat/m1-multipart-upload`.

## Execution Order

### Slice 1: Schema ownership and migration contract

**Observable behavior**: SQLAlchemy metadata owns the first business schema and Alembic
can upgrade, downgrade one M1 revision, and re-upgrade without mutating M0.

- **Red**: Add migration/model contract tests for table/column/constraint/index names,
  tenant columns, unique idempotency/version links, non-negative quota checks, and M0
  revision immutability.
- **Green**: Add declarative base, timestamp helpers, Tenant/User/Membership,
  Document/DocumentVersion, UploadSession/UploadPart models, session factory, metadata
  import in Alembic, and `20260717_0002_multipart_upload.py`.
- **Refactor**: Keep shared base/session mechanics in `db`; keep identity/document/upload
  fields in their domain modules.
- **Validate**:

  ```powershell
  uv run pytest packages/core/tests/test_upload_models.py
  uv run pytest tests/multipart/test_m1_migration.py -m integration
  uv run mypy packages/core/src
  ```

- **Rollback point**: Downgrade only revision 0002 in the disposable local database;
  never rewrite revision 0001.

### Slice 2: Real principal resolution

**Observable behavior**: A signed bearer token resolves only through an active persisted
membership and enriches request context without leaking the token.

- **Red**: Add tests for missing/malformed/oversized token, wrong algorithm/signature,
  issuer/audience/time/UUID failures, inactive tenant/user/membership, tenant mismatch,
  valid resolution, request-context enrichment, safe logs/spans, and local bootstrap
  environment restrictions.
- **Green**: Add JWT settings/codec, membership query, FastAPI dependency, typed 401/403
  errors, principal context enrichment, and `scripts/bootstrap_local_principal.py`.
- **Refactor**: Separate pure token validation from the PostgreSQL membership lookup and
  keep HTTP header mapping API-owned.
- **Validate**:

  ```powershell
  uv run pytest apps/api/tests/test_auth_contract.py packages/core/tests/test_principal_context.py
  uv run pytest tests/multipart/test_auth_integration.py -m integration
  uv run ruff check apps/api packages/core scripts/bootstrap_local_principal.py
  uv run mypy packages/core/src apps/api/src
  ```

- **Rollback point**: Remove business router registration; health endpoints must remain
  usable without a fabricated principal.

### Slice 3: File policy, part plan, and quota reservation

**Observable behavior**: Valid TXT/PDF/DOCX metadata produces a deterministic safe part
plan and exactly one quota reservation; unsafe or conflicting input produces a typed
error before upload initiation.

- **Red**: Add table-driven policy tests, part-boundary/10,000-part tests, normalized
  request fingerprint tests, concurrent idempotency tests, quota race tests, and object
  key privacy tests.
- **Green**: Implement upload settings, filename/media/hash validation, part planning,
  random object keys, create-session service, tenant row locking, idempotent replay, and
  typed API request/response/error schemas.
- **Refactor**: Keep pure validation independent from database/S3 effects; centralize
  camelCase API model configuration.
- **Validate**:

  ```powershell
  uv run pytest packages/core/tests/test_upload_policy.py apps/api/tests/test_upload_create_contract.py
  uv run pytest tests/multipart/test_upload_create_integration.py -m integration
  uv run mypy packages/core/src apps/api/src
  ```

- **Rollback point**: Delete only initializing test sessions through the cleanup command
  or disposable database reset; do not remove shared volumes automatically.

### Slice 4: S3/MinIO multipart adapter and browser CORS

**Observable behavior**: The pinned MinIO release enforces checksum-bound multipart PUTs
and exposes the response headers needed by the browser.

- **Red**: Add adapter protocol tests for all-page ListParts/ListMultipartUploads loops
  and a real MinIO feature probe for create, checksum-bound presign, PUT, server-side
  truncation capability, list/checksum/ETag aggregation, complete, head checksum, range
  read, abort, and incomplete-upload listing. Add Compose contract tests for pinned
  images and the exact-origin community CORS profile, while recording the lack of
  `PutBucketCors` support.
- **Green**: Implement the async boto3 adapter, service/presign endpoints, timeouts,
  SigV4 path-style clients, thread offload, CORS configuration, and compatible pinned
  MinIO/mc images.
- **Refactor**: Normalize botocore errors at one boundary and ensure signed URLs/object
  identifiers never enter logs or traces.
- **Validate**:

  ```powershell
  docker compose -f infra/compose/docker-compose.yml up -d --wait
  docker compose -f infra/compose/docker-compose.yml --profile init run --rm minio-init
  uv run pytest packages/core/tests/test_multipart_adapter.py
  uv run pytest tests/multipart/test_minio_multipart.py -m integration
  docker compose -f infra/compose/docker-compose.yml down
  ```

- **Rollback point**: Abort probe uploads and restore the last tested pinned image; retain
  no mutable `latest` claim in evidence.

### Slice 5: Presign, list, and resume API

**Observable behavior**: An authenticated owner can sign valid missing parts and query
server-observed parts; other actors/tenants cannot use the session ID.

- **Red**: Add API tests for owner/tenant boundaries, expiry, part range/size/checksum,
  repeated presign, mismatched checksum, ListParts reconciliation and pagination, typed
  error mapping, and log redaction.
- **Green**: Add upload router/dependencies, presign and get-session services, UploadPart
  expectation/upsert logic, S3 reconciliation, CORS methods/headers, and response runtime
  contracts.
- **Refactor**: Keep database transaction duration separate from object-store I/O and
  reuse one authorization query shape.
- **Validate**:

  ```powershell
  uv run pytest apps/api/tests/test_upload_session_contract.py
  uv run pytest tests/multipart/test_upload_resume_integration.py -m integration
  uv run ruff format --check packages/core apps/api
  uv run ruff check packages/core apps/api
  uv run mypy packages/core/src apps/api/src
  ```

- **Rollback point**: Abort only M1 test sessions; health/readiness behavior remains
  unchanged.

### Slice 6: Completion, envelope validation, and crash reconciliation

**Observable behavior**: Verified parts complete into one uploaded document version;
duplicates and a process failure after S3 completion reconcile to the same result.

- **Red**: Add tests for missing/extra/reordered/duplicate parts, ETag/checksum/size
  mismatch, expiry, invalid head metadata, PDF/TXT signatures, bounded DOCX central
  directory and ZIP-bomb/path policy, duplicate/concurrent complete, simulated
  post-S3/pre-DB crash, and quota conversion.
- **Green**: Implement `completing` transition, complete/reconcile flow, bounded S3 range
  reader, file-envelope validators, Document/DocumentVersion finalization, unique-race
  recovery, and stable error cleanup.
- **Refactor**: Separate object verification from database finalization; make failure
  injection explicit in tests rather than production flags.
- **Validate**:

  ```powershell
  uv run pytest packages/core/tests/test_document_envelope.py apps/api/tests/test_upload_complete_contract.py
  uv run pytest tests/multipart/test_upload_complete_integration.py -m integration
  uv run mypy packages/core/src apps/api/src
  ```

- **Rollback point**: Delete only invalid M1 test objects after metadata ownership is
  verified; never delete an existing completed version as an abort fallback.

### Slice 7: Abort, expiration, and orphan cleanup

**Observable behavior**: Abort and cleanup are rerunnable, release quota once, reconcile
stale completion, and remove only eligible incomplete multipart uploads.

- **Red**: Add state-transition tests for repeated abort, completed conflict, expiration,
  initialization failure, stale completing with/without object, paginated orphan scan,
  concurrent cleanup workers, and partial object-store failure.
- **Green**: Implement abort endpoint/service and `scripts/cleanup_uploads.py` with bounded
  batches, skip-locked rows, grace periods, dry-run output, structured counters, and
  stable non-zero failures.
- **Refactor**: Share reconciliation primitives with complete; avoid a second state
  machine in the cleanup script.
- **Validate**:

  ```powershell
  uv run pytest apps/api/tests/test_upload_abort_contract.py packages/core/tests/test_upload_cleanup.py
  uv run pytest tests/multipart/test_upload_cleanup_integration.py -m integration
  uv run python scripts/cleanup_uploads.py --dry-run
  ```

- **Rollback point**: Stop cleanup on ambiguous ownership; leave the row for manual
  review instead of deleting a potentially valid object.

### Slice 8: Browser hashing and upload state machine

**Observable behavior**: The browser hashes incrementally, schedules at most four part
uploads, pauses/retries/resumes legally, and persists no secret material.

- **Red**: Add Vitest tests for every reducer transition, invalid commands, concurrent
  scheduler cap, progress aggregation, XHR headers/ETag/errors/abort, Web Worker protocol,
  bounded file slicing, refresh persistence schema, and different-file rejection.
- **Green**: Add incremental hash dependency, typed Worker, pure reducer/effect commands,
  XHR transfer adapter, scheduler, session persistence, typed upload API client, and
  session-token storage.
- **Refactor**: Keep network/schema parsing at boundary modules and keep React rendering
  free of raw transport logic.
- **Validate**:

  ```powershell
  pnpm --filter web test -- src/upload
  pnpm --filter web lint
  pnpm --filter web typecheck
  ```

- **Rollback point**: Remove upload feature entry points while preserving the M0
  readiness client and dashboard tests.

### Slice 9: Operational upload UI and browser recovery

**Observable behavior**: A user can authenticate locally, upload, pause, refresh,
reselect, resume missing parts, cancel, and complete from a stable responsive UI.

- **Red**: Add Testing Library tests for token/session states and Playwright tests for a
  real interrupted/reloaded upload plus wrong-file rejection and desktop/mobile layout.
- **Green**: Add `UploadWorkspace`, compact auth control, upload toolbar, progress/part
  views, accessible icon actions/tooltips, existing readiness integration, responsive
  styles, and Playwright configuration/fixtures.
- **Refactor**: Remove explanatory/marketing copy and keep repeated part rows as the only
  card collection.
- **Validate**:

  ```powershell
  pnpm --filter web test
  pnpm --filter web lint
  pnpm --filter web typecheck
  pnpm --filter web build
  pnpm --filter web exec playwright test
  ```

- **Manual visual gate**: Review screenshots at 1440x900 and 390x844 for non-overlap,
  stable controls, visible progress/errors, and a usable resumed state.
- **Rollback point**: Keep backend upload APIs available while restoring the last green
  Web shell if browser recovery cannot be made deterministic.

### Slice 10: Real smoke, CI gate, evidence, and spec capture

**Observable behavior**: One documented command runs the full local M1 path and produces
an immutable, machine-validated evidence record.

- **Red**: Add contracts requiring the M1 smoke command, CI integration job, evidence
  schema/index/artifacts, exact reviewed commit, measured memory report, source URLs,
  screenshots, limitations, and factual Trellis specs.
- **Green**: Add `scripts/multipart_smoke.py`, root commands, the small service-integration
  CI job, 1 GiB local run option, evidence manifest/artifacts, README workflow, and
  proven upload/auth/database/frontend specifications.
- **Refactor**: Reuse one environment/startup helper where it removes real duplication;
  keep M0 evidence immutable.
- **Validate**:

  ```powershell
  uv sync --frozen
  pnpm install --frozen-lockfile
  uv run ruff format --check .
  uv run ruff check .
  uv run mypy packages/core/src apps/api/src apps/worker/src
  uv run pytest -m "not integration"
  pnpm lint
  pnpm typecheck
  pnpm test
  pnpm build
  uv run pytest tests/multipart -m integration
  uv run python scripts/multipart_smoke.py --run --size-bytes 1073741824 --interrupt-after-parts 2 --measure-api-rss
  pnpm --filter web exec playwright test
  uv run pytest tests/multipart/test_m1_evidence_contract.py
  uv run python .trellis/scripts/task.py validate 07-17-m1-multipart-upload
  git status --short
  ```

- **Rollback point**: Keep the last reviewed code commit runnable. Evidence generation
  never mutates or deletes an earlier manifest, and named volumes are not removed
  automatically.

## Review Gates

### Gate A: Scope

- M1 contains no Job/Outbox/Celery/lease/RAG/Agent/deployment behavior.
- `DocumentVersion.status` is uploaded, not ready.
- Whole-file SHA-256 remains explicitly unverified until M3.

### Gate B: Security

- Every business route requires a real persisted principal.
- Cross-tenant and actor-mismatch tests pass.
- Tokens, signed URLs, upload IDs, object keys, filenames, and bodies do not appear in
  normal logs/traces/evidence.

### Gate C: Reliability

- Create, complete, abort, quota, and cleanup are idempotent.
- Post-S3/pre-DB crash reconciliation is proven.
- Unknown ownership never triggers deletion.

### Gate D: Direct transfer and memory

- Browser/streaming client sends bytes directly to MinIO/S3.
- Part concurrency is bounded.
- The 1 GiB run records real API RSS samples and limitations without capacity claims.

### Gate E: Delivery evidence

- Locked quality, integration, browser, smoke, evidence, and Trellis checks pass.
- Specs describe only patterns proven by M1 code/tests.
- Reviewed code and evidence commits are intentional and independently identifiable.

## Completion Rules

- Do not archive M1 until the user-visible workflow, real MinIO integration, 1 GiB
  smoke, browser recovery, and evidence contract are all green.
- Do not hide a failure with skip, xfail, allow-failure, or repeated reruns.
- If the 1 GiB run cannot execute because of local disk/resource limits, record a
  blocking evidence gap; a smaller run does not satisfy that acceptance criterion.
- After M1 is archived, plan M2 from the actual completion transaction and models.
