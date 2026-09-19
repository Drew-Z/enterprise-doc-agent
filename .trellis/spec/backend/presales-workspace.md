# Presales workspace: current local implementation

Implemented by `09-12-saas-presales-workspace` on 2026-09-12 and extended with real
file ingestion by `09-12-saas-presales-ingestion` on 2026-09-13. This is a bounded
workflow within an existing enterprise session. Separate local tasks now provide
admission, browser OIDC, invitations and commercial request quotas. Payment,
automatic requirement extraction and production model/customer acceptance remain
outside this workflow. Commercial contracts are in [entitlements-usage.md](entitlements-usage.md).

## Ownership and public contract

- Core: `packages/core/src/enterprise_doc_core/presales/{schemas,access,gateway,generation,service,export}.py`.
- API: `apps/api/src/enterprise_doc_api/presales/router.py`, wired in app/config.
- Tables: `presales_packets`, `presales_rows`, `presales_attempts`, `presales_reviews`.
- Migrations `20260912_0021` and `20260912_0022` have been applied locally. Append
  migrations for subsequent changes; do not edit these revisions.

| Method/path | Contract |
|---|---|
| GET /api/presales | Up to 50 recent sheets belonging to the current tenant and author |
| POST /api/presales | Fixed title, 1–6 version/applicability pairs, 1–12 key/text/location requirements |
| GET /api/presales/{id} | Reauthorized sources, rows, attempts and review history |
| POST /api/presales/{id}/rows/{row}/generate | One explicit row attempt, Idempotency-Key required |
| PUT /api/presales/{id}/rows/{row}/review | expectedRevision plus response text; Idempotency-Key required |
| GET /api/presales/{id}/export?mode=draft\|reviewed | Reauthorized UTF-8 BOM CSV attachment |

JSON uses camelCase and rejects extra fields. Creation requires Idempotency-Key;
changed payloads with a reused creation/review key conflict. A generated draft is
not overwritten by regeneration. Sources and requirements are fixed; changes
require a new sheet.

## Identity and source validity

The database checks active Tenant, User and Membership, then matches both tenant
and original author. A different owner in the same tenant cannot read the sheet.
Document visibility reuses `document_visible_to_actor`; client roles are not an
authorization substitute. Rows/attempts/reviews use tenant-composite foreign keys.

Each source must be a ready version with an active succeeded/ready generation.
Snapshots contain document/version/generation IDs, filename, content SHA, version
number, latest version number at creation, and user-provided applicability.
Explicit old ready versions are allowed; a later version or changed generation
invalidates the snapshot. ORM source refresh uses populate_existing so a second
check within one session sees changed values.

Reads, preparation for model dispatch, result commit, review and export recheck
permissions and snapshots. Missing access produces 403/404; changed snapshots
produce 409. Evidence is not returned after these checks reject access. This is
request-time authorization, not a promise to recall bytes previously downloaded.

## Uploaded files and content integrity

New TXT, text PDF and DOCX sources use the existing browser multipart upload,
MinIO, transactional Outbox, Redis/Celery delivery and document ingestion pipeline.
Upload completion creates an uploaded version; it does not make that version a
presales source or certify its full-file SHA. The Worker reads the complete bounded
spool and compares SHA-256 with the version's declared hash before parsing,
chunking or embedding. A mismatch fails permanently as `document_sha256_mismatch`.

`DocumentIngestionService._persist_chunks` locks the tenant-bound version and
generation, rechecks the declared hash and records `content_sha256_verified_at`
with the chunk checkpoint. Activation requires this marker. An old EMBED checkpoint
without the marker must download and verify again; a verified checkpoint can reuse
its saved chunks for an embedding retry. This change does not backfill or invalidate
historical ready generations, and presales does not impose a new verification-marker
requirement on those historical sources.

An uploaded, processing or failed version is unavailable for sheet creation
(`404 presales_source_unavailable`). A PDF with a valid envelope but invalid body
can complete upload and then fail parsing as `pdf_parse_failed`; uploading a correct
file creates a new document/version. TXT and DOCX headings and PDF page numbers
survive ingestion and are exposed with the actual retrieved evidence.

## Generation and accounting

The API runs one bounded row at a time; there is no new worker queue. Claim an
attempt in a short tenant/row transaction, release it for retrieval/network I/O,
then reauthorize and fence by attempt state/deadline before saving the draft.
An expired execution cannot overwrite a newer attempt. Same-key replay does not
call the provider again. Default limits: 3 attempts per row, 2 live attempts per
tenant, 100 attempts per UTC day; failures count toward these development budgets.

`ApiSettings.presales.generation_enabled` defaults to false. Enabling generation
requires an OpenAI-compatible primary model configuration; deterministic mode
returns `presales_model_not_configured`. No fake model answer is used in product
mode. Default row deadline is 90 seconds (maximum configurable 180 seconds).

Hybrid retrieval runs separately for every selected version. At most 2 candidates
per version and 1800 characters per candidate are sent. Candidate tenant, version
and generation IDs are checked. Saved retrieval notes disclose candidate counts
and truncation. Applicability and document content are untrusted input; file order
or date does not establish legal precedence.

The dedicated Chat Completions adapter sends one system/user pair, JSON mode,
tools=[], tool_choice=none, stream=false and max_tokens=4000. The serialized
request is capped at 128 KiB and the response at ModelSettings.max_output_bytes.
Only one completed stop choice is accepted. Tool calls, refusal, truncation,
invalid JSON/schema or citations fail the row. No repair, fallback or retry occurs.

Statuses: supported, conditional, contradicted, insufficient_evidence,
conflicting_evidence. Conditional needs conditions; insufficient evidence needs
missingInformation; other statuses need citations; conflict needs two versions.
`validate_citations` binds exact excerpts to authorized candidates. These checks
do not prove logical entailment or complete capture of contractual conditions.

Attempts store model provider/name, pipeline and prompt versions, prompt SHA,
configured model version/revision and returned model/response ID when available.
Configured or returned identifiers do not authenticate upstream model weights.
Index generation IDs are in source snapshots. Deployment commit/image identity
is recorded by release evidence, not inferred from a dirty working tree.

providerRequestCount is an observed client dispatch count, not a remote execution
or billing count. It is 0 before dispatch, temporarily NULL once dispatch is
prepared, and 0/1 once the outcome is observed. Preflight rejection remains 0;
interruption/crash may leave NULL. A late expired execution may update accounting
but cannot save a draft. Missing token usage remains null. None of these fields
constitutes a payment ledger. The separate commercial request ledger now reserves
in the attempt-creation transaction and settles with a successful validated draft;
failures/cancellation release quota while provider observations remain independent.

Only tenants with no entitlement history retain legacy behavior. Configured but
not currently active periods reject new generation with HTTP 403
`presales_entitlement_inactive`; insufficient quota is HTTP 429
`presales_usage_limit`, and usage-service failure is HTTP 503
`presales_usage_unavailable`. Preflight rejection rolls back the attempted row and
does not dispatch the model. Expiry does not block authorized reads, review, export
or replay of an already generated draft. Reservations retain their original period.

## Review, export and diagnostics

Original drafts are immutable. Human reviews append actor/time/text/status/note
and revision, with expectedRevision conflict protection. Basic evidence/status
constraints still apply; semantic approval belongs to the reviewer. Maximum 100
reviews per row. Reviewed export requires every row to have a draft and review.

CSV uses Python csv, UTF-8 BOM, a fixed attachment filename and no-store. Cells
with tested formula/control prefixes are neutralized with a leading apostrophe.
Requirements, locations, effective text, conditions, missing information, evidence,
source snapshots, review metadata and original model text are retained. Export
does not create a public object URL. Audit events retain IDs/status/counts and
request/correlation IDs, excluding document and response bodies.

## Validation boundary

Core contract tests, PostgreSQL/ASGI integration, fetch-boundary Web tests and
Chromium desktop/mobile tests are recorded in each task's validation.json. The
original workspace browser suite uses seeded ready documents and an injected
principal resolver. The separate ingestion suite creates all document versions
through real upload/parser/Worker execution and uses local JWTs with the default
DatabasePrincipalResolver. Both use HashEmbeddingProvider and httpx.MockTransport;
neither proves external model quality, commercial IdP authentication or customer
acceptance. See the [real ingestion test contract](../foundation-tests/backend/presales-ingestion.md).

Run with local dependencies ready and the schema upgraded:

```powershell
& .\.venv\Scripts\python.exe -X utf8 -B -m pytest packages/core/tests/test_presales_contract.py tests/presales/test_presales_workflow_integration.py -q
pnpm --filter web exec playwright test --config playwright.presales.config.ts
pnpm --filter web exec playwright test --config playwright.presales-ingestion.config.ts
```

The original browser harness uses loopback ports 18765/18073. The ingestion harness
uses 18766/5173, publishes only its own tenant's Outbox events and uses a unique Redis
key prefix. An event in the other test tenant remains pending. Both refuse server
reuse and explicitly remove their own resources. Harnesses live under tests and
are never registered in the product API entrypoint.

## Proven Examples

- `packages/core/src/enterprise_doc_core/documents/ingestion_service.py`: complete
  content verification, legacy checkpoint recovery and verified activation.
- `tests/jobs/test_ingestion_content_integrity.py`: same-length changed bytes are
  rejected for new ingestion and an unverified legacy EMBED checkpoint.
- `tests/presales/test_presales_workflow_integration.py`: tenant/author/source
  authorization, attempts, immutable drafts, review revisions and CSV export.
- `tests/presales/ingestion_server.py` and
  `apps/web/presales-ingestion-e2e/workspace.spec.ts`: real uploaded sources, passage
  metadata, review/export/recovery, parser failure and tenant-scoped teardown.
