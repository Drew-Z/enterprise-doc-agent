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
| R-24 | Implemented locally | 196 non-integration backend tests, 49 real multipart integration tests, 124 Web tests, lint/typecheck/build, and one real Playwright workflow pass. The new remote `m1-integration` job is defined but cannot be claimed executed until pushed. |
| R-25 | Partial | Generated 17 MiB restart/resume smoke passed twice. The second run observed API RSS between 123,289,600 and 125,161,472 bytes with a maximum within-generation delta of 1,638,400 bytes. The required reviewed-commit 1 GiB run is pending. |
| R-26 | Pending reviewed commit | Manifest/index/artifact contract must be added only after the implementation commit exists and exact commands are rerun against it. M0 evidence remains unchanged. |

## Current Gate

The implementation/CI/documentation portion of Slice 10 is ready for commit review.
M1 cannot be archived and R-25/R-26 cannot be marked complete until the reviewed commit
exists, the real 1 GiB run passes, all evidence artifacts are digested, and the final
evidence contract validates that immutable record.
