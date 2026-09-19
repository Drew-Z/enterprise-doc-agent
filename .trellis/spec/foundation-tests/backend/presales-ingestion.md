# Presales real-file ingestion acceptance

## 1. Scope / Trigger

Use this contract when changing the uploaded-file path into the presales workspace,
full-file integrity checks, document readiness refresh or source preselection. The
test composes existing product services under a separate test entrypoint. All
documents come from browser uploads; no ready version, generation or chunk is seeded.
TXT, text PDF and DOCX fixtures are synthetic. OCR, commercial OIDC, external model
quality/cost and customer acceptance are outside this acceptance run.

## 2. Signatures

- `DocumentIngestionService._execute(claim)` verifies the complete spool before parse.
- `_persist_chunks(..., verified_content_sha256: str)` binds verification to the
  tenant-bound version/generation checkpoint; `_commit_embeddings_and_activate(...)`
  requires a verification marker.
- `DocumentsPage` receives `contextKey` and `onStartPresales(versionId)` from App.
- `PresalesWorkspace` receives `initialVersionId` and `onInitialVersionConsumed`.
- `tests.presales.ingestion_server` exposes the real API on loopback port 18766 with
  local `JwtTokenCodec` credentials and the default `DatabasePrincipalResolver`.
- `apps/web/playwright.presales-ingestion.config.ts` starts that API and a dedicated
  Vite configuration on port 5173, refuses server reuse and runs one browser worker.
- `PRESALES_INGESTION_OUTPUT_DIR` selects a fresh result directory; the harness writes
  `run-context.json` and `cleanup.json`, and Playwright writes `results.json`.

## 3. Contracts

The browser uses the actual File input, SHA Worker, upload-session APIs, MinIO PUT
and complete calls. A tenant-scoped `TenantOutboxStore` selects only this run's
event IDs and delegates claims to `OutboxService`; actual `OutboxPublisher`,
`CeleryTaskDispatcher`, Redis, Celery and `JobDeliveryConsumer` execute ingestion.
Pausing the publisher provides a pending-state assertion without rewriting Job or
document state. Redis uses a unique `presales-ingestion-<run-id>:` global key prefix
that must initially be empty. Another test tenant owns a sentinel event that must
remain pending with zero attempts. No global queue flush or unrestricted publisher
is allowed.

Loopback endpoints are validated before using existing local infrastructure. Port
5173 matches the existing MinIO CORS allowlist; an occupied port fails setup rather
than reusing a server or changing MinIO. The fixture uses a solo Celery worker with
concurrency one and disabled remote control/events. It does not modify `.env`,
global configuration, applied migrations or production API registration.

Upload completion retains the declared hash without marking it verified. The Worker
compares all downloaded bytes with that declaration before parsing/chunking/embedding.
The chunk transaction locks version then generation, rechecks the declaration and
records `content_sha256_verified_at`. Unverified legacy EMBED checkpoints must
download and verify again; verified checkpoints preserve the ordinary embed retry.
Historical ready generations are not backfilled or invalidated by this change.

An explicit document entry starts a new sheet, preselects only the still-available
ready version and still requires source-applicability confirmation. The entry is
consumed once and bound to tenant/actor/auth revision. Inventory errors stop polling
and retain manual retry; ready/failed terminal states stop polling too.

The model transport is `httpx.MockTransport`. It obtains its excerpt and IDs from
the actual retrieved evidence and fails if the expected passage is absent. Output
is labelled controlled acceptance output. Embeddings use `HashEmbeddingProvider`.
These calls count as controlled fixture calls, never as external model requests,
semantic accuracy, production latency or paid usage.

Teardown stops the publisher and worker, closes their resources, and removes only
objects/multipart uploads, database rows and Redis keys owned by the run. It clears
the upload reverse link before removing documents, tenants and users. The cleanup
receipt asserts worker termination and zero remaining test tenants, users, objects,
multipart uploads and prefixed Redis keys. Existing local infrastructure stays up.

Trace capture is disabled. Playwright failure snapshots can still contain password
input values, so tests clear the visible token draft after saving it and clear
password inputs in `afterEach`. Retained text evidence is scanned for JWTs and
signed object URLs before archival; failures preserve useful redacted diagnostics.

## 4. Validation & Error Matrix

| Trigger | Required observable result |
|---|---|
| Publisher paused after upload | Uploaded version, no generation; pending Job/Outbox and disabled source entry |
| Unready or foreign source used to create a sheet | HTTP 404 `presales_source_unavailable` |
| Foreign tenant reads an existing sheet | HTTP 404 `presales_not_found` |
| Same-length object content differs from declared SHA | Permanent `document_sha256_mismatch`; no embedding call, active generation or chunks |
| Legacy EMBED checkpoint has no verification marker | Re-download and verify; changed bytes fail instead of reusing stale chunks |
| Valid PDF signature, broken body | `pdf_parse_failed`, failed/inactive generation, dead Job, zero chunks |
| Correct file uploaded after parse failure | A new document/version becomes ready and is selectable; failed source stays excluded |
| Inventory read fails | Automatic polling stops; explicit retry can recover |
| Outbox from the other test tenant | Sentinel remains pending with zero attempts |
| Teardown is incomplete | Acceptance fails and retains the exact cleanup receipt |

## 5. Good / Base / Bad Cases

- Good: three actual uploads become ready without a manual refresh. Source snapshots
  and object readback match original SHA values. TXT/DOCX headings and PDF page 1
  reach exact citations; three reviews survive reload and appear in a BOM CSV with
  formula-prefix neutralization.
- Base: a 390px browser uploads a PDF that passes envelope validation but fails
  parsing. Its source action is disabled. Re-uploading a correct PDF creates a new
  selectable version and produces a controlled response with page metadata.
- Bad: replacing `30 days` with same-length `90 days` in the object cannot be
  accepted because its size or upload transport checksum looked valid earlier.
- Bad: seeded ready rows, browser route stubs, a custom principal resolver or a
  manually succeeded Job cannot stand in for this test's ingestion/authentication
  boundaries. They remain valid only in separately labelled narrower tests.

## 6. Tests Required

```powershell
& .\.venv\Scripts\python.exe -X utf8 -B -m pytest tests/jobs/test_ingestion_content_integrity.py tests/jobs/test_m3_ingestion_integration.py -q
& .\.venv\Scripts\python.exe -X utf8 -B -m pytest packages/core/tests/test_presales_contract.py tests/presales/test_presales_workflow_integration.py -q
pnpm --filter web exec playwright test --config playwright.presales-ingestion.config.ts
```

Also run the repository's non-integration pytest gate, Ruff format/check, strict
mypy, Web lint/typecheck/tests/build, and Trellis context validation. Preserve the
final code's raw browser JSON, pipeline/recovery evidence, CSV and desktop/mobile
screenshots. Inspect screenshots as well as overflow assertions. Earlier failed
runs remain failures even after later runs pass. Review baseline file hashes and
worktree identities before local delivery; do not rewrite previous task evidence.

## 7. Wrong vs Correct

Wrong: a successful multipart complete response proves the whole file's content
hash, or an EMBED checkpoint always permits skipping object download.

Correct: upload completion validates its bounded transport/envelope contract. The
Worker hashes the full spool, persists verification with its chunks, and only
resumes embedding without download when that checkpoint was verified.

Wrong: a passed browser run with four controlled responses proves the external
model, real commercial login or customer acceptance.

Correct: report each real and controlled boundary separately. Preserve unknown
model cost and the outstanding commercial/customer gates.

## Proven Examples

- `packages/core/src/enterprise_doc_core/documents/ingestion_service.py`
- `tests/jobs/test_ingestion_content_integrity.py`
- `tests/presales/ingestion_fixtures.py`
- `tests/presales/ingestion_server.py`
- `apps/web/presales-ingestion-e2e/workspace.spec.ts`
- `apps/web/presales-ingestion-e2e/teardown.ts`
- `apps/web/src/App.presales.test.tsx`
- `apps/web/src/product/DocumentsPage.test.tsx`
- `apps/web/src/presales/PresalesWorkspace.test.tsx`
