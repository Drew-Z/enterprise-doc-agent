# M1 Acceptance Audit

Audit date: 2026-07-18

Statuses distinguish implementation and local testing from reviewed-commit evidence.
`Implemented` does not mean production capacity has been proven.

| Requirement | Status | Code and executable evidence |
|---|---|---|
| R-1 | Implemented | Persisted identity models, JWT validation, membership lookup, `apps/api/tests/test_auth_contract.py`, and `tests/multipart/test_auth_integration.py`. |
| R-2 | Implemented | Tenant/actor-scoped upload queries and hidden-boundary tests across create, resume, complete, abort, and cleanup suites. |
| R-3 | Implemented | `scripts/bootstrap_local_principal.py` is local/test restricted; token output is captured in browser/smoke bootstrap and excluded from tracked artifacts. |
| R-4 | Implemented | Idempotency-key request fingerprinting and concurrent replay/conflict tests in create contract/integration suites. |
| R-5 | Implemented | TXT/PDF/DOCX filename, media, size, digest, path, quota, and part-count policy tests in `packages/core/tests/test_upload_policy.py`. |
| R-6 | Implemented | Tenant locking, reservation conversion/release, and create/complete/abort/cleanup concurrency tests. |
| R-7 | Implemented | Server part planning and random server-owned object keys in upload policy/service tests. |
| R-8 | Implemented | Boto3 multipart initiation and short-lived presign; browser and API receive no object-store credentials. |
| R-9 | Implemented | Per-part SHA-256 expectation persistence, presign binding, retry equality, and conflict rejection tests. |
| R-10 | Implemented | Paginated ListParts reconciliation, generation ordering, mismatch invalidation, and real MinIO resume tests. |
| R-11 | Implemented | Pinned MinIO image, exact server-level local origins, exposed ETag/checksum behavior, and documented production limitation. |
| R-12 | Implemented | Ordered client/object-store part verification before completion in API, service, adapter, and integration tests. |
| R-13 | Implemented | Missing multipart reconciliation through HeadObject and post-object/pre-database crash retry tests. |
| R-14 | Implemented | Bounded TXT/PDF/DOCX envelope validation and adversarial ZIP tests. |
| R-15 | Implemented | Per-part transport integrity is verified; whole-content digest remains explicitly client-declared until M3. |
| R-16 | Implemented | Unique document/version/session links, quota finalization, and duplicate/concurrent completion tests prove one effective result. |
| R-17 | Implemented | Idempotent DELETE semantics and completed-version protection in abort contract/integration tests. |
| R-18 | Implemented | `scripts/cleanup_uploads.py` supports bounded claims, expiry/completing/orphan reconciliation, safe retries, and secret-safe reports. |
| R-19 | Implemented | `UploadWorkspace` provides token entry, selection, progress, four-way upload, pause/resume/retry/cancel/complete, and clear-completed controls. |
| R-20 | Implemented | Web Worker hashing uses bounded slices and never reads the complete large file into one ArrayBuffer. |
| R-21 | Implemented | Strict non-secret recovery metadata, explicit reselection, filename/size/digest verification, reconciliation, and wrong-content browser rejection. |
| R-22 | Implemented | Pure reducer legality, typed failures, generation/attempt fencing, scheduler pause, and server abort only on cancel. |
| R-23 | Implemented | Principal enrichment occurs after persisted resolution; formatter redaction and auth/request tests exclude sensitive headers and fields. Successful smoke evidence does not retain raw API path logs. |
| R-24 | Complete locally | Against reviewed commit `ca43716265d7057aa79288bae054fc6ae0c5056d`, 198 non-integration backend tests, 49 real multipart integration tests, 124 Web tests, lint/typecheck/build, and one real Playwright recovery workflow passed. The remote `m1-integration` job is defined but has not been claimed as executed because this repository has no configured remote. |
| R-25 | Complete | The reviewed-commit 1 GiB smoke uploaded 64 x 16 MiB parts, interrupted after two, restarted the API once, reconciled two existing parts, uploaded only the remaining 62, and replayed completion successfully. Across 64 samples, API RSS was 123,355,136 to 125,722,624 bytes with a maximum within-generation delta of 1,994,752 bytes. See `evidence/m1/artifacts/multipart-smoke-1g-report.json`; this single-machine observation is not a load test or capacity claim. |
| R-26 | Complete | Immutable manifest `evidence/m1/20260718-151000-m1-multipart-upload.json` records reviewed commit `ca43716265d7057aa79288bae054fc6ae0c5056d`, exact successful commands, source URLs, limitations, visual gates, and SHA-256 digests for every referenced M1 artifact. `evidence/index.json` contains one passed M1 entry; M0 evidence remains unchanged. |

## Current Gate

Local M1 acceptance is locked to reviewed implementation commit
`ca43716265d7057aa79288bae054fc6ae0c5056d`. The 1 GiB smoke, deterministic quality
gates, real PostgreSQL/MinIO integration, browser recovery, evidence contract, and
Trellis validation are represented by the immutable M1 evidence record. The task remains
unarchived in this change: remote GitHub Actions execution, public deployment, sustained
load, and production capacity are not claimed.
