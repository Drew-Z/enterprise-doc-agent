# Multi-document Analysis Workspace (A1-A5 local candidate and runners, 2026-10-13)

## 1. Scope / Trigger

Core `analysis/` owns project drafts, immutable source snapshots, fixed evidence
execution, the optional bounded Agent mode, immutable human revisions, frozen XLSX
delivery, dependency invalidation and conservative cross-snapshot reuse. API exposes
project, source, run, input resume, matrix, cancellation, finite SSE, exact review and
artifact operations. Worker executes one unit per object/field pair through the durable
Job lease. React exposes preparation, fixed/Agent selection, input collection, revision
review, delivery recovery and incremental-update reasons.

Migrations `0038` through `0043` are additive local candidates. They implement
structured document generations, source snapshots, runs, units, provider-linked
actions, cell revisions, reviews, public events, input requests, tool receipts,
analysis artifacts, exact artifact approvals, dependency fingerprints and reuse
lineage.

Both admission switches default off:

- `UPLOAD__STRUCTURED_DOCUMENTS_ENABLED=false` gates new projects, edits, source
  confirmation and parser 2 admission.
- `ANALYSIS__EXECUTION_ENABLED=false` gates new runs. Execution also requires
  structured uploads, a fully configured OpenAI-compatible primary route and an
  `analysis_cell` product quota when the tenant has a commercial period.

No production flag or migration was applied by this task. Controlled tests use a
local hash embedding boundary and controlled HTTP. A5 holdout/scoring and capacity
runners are locally validated, but no real-model holdout or external 4C4G execution
was performed; they do not establish model quality, production capacity or customer
acceptance.

## 2. Signatures

Implemented HTTP surface under `/api/analysis`:

```text
GET    /capabilities
GET    /projects
POST   /projects
GET    /projects/{project_id}
PATCH  /projects/{project_id}                    If-Match: "<revision>"
POST   /projects/{project_id}/snapshots          If-Match: "<revision>"
GET    /projects/{project_id}/snapshots
GET    /snapshots/{snapshot_id}/sources/{version_id}/blocks

POST   /projects/{project_id}/runs               Idempotency-Key: <printable ASCII>
GET    /projects/{project_id}/runs
GET    /runs/{run_id}
GET    /runs/{run_id}/matrix
GET    /runs/{run_id}/events                     Last-Event-ID: <decimal>
POST   /runs/{run_id}/cancel
POST   /runs/{run_id}/inputs
POST /cells/{cell_id}/revisions
GET  /cells/{cell_id}/revisions
POST /cells/{cell_id}/reviews
POST /runs/{run_id}/exports
GET  /runs/{run_id}/artifacts
POST /artifacts/{artifact_id}/approvals
GET  /artifacts/{artifact_id}/download
```

A5 offline and operator-run signatures:

```powershell
python scripts/evaluate_analysis_holdout.py --plan <plan.json> --output <execution.json> [--execute]
python scripts/score_analysis_holdout.py --execution <execution.json> --reference <reference.json> --output <score.json>
python scripts/run_analysis_capacity.py --plan infra/capacity/analysis-4c4g.example.yaml
python scripts/run_analysis_capacity.py --plan <plan.yaml> --output-dir <dir> --external-execution <reviewed external arguments>
python scripts/validate_recovery_capacity_evidence.py <report.json>
```

The plan-only capacity command performs no workload and must return
`production_capacity_approved=false`. External execution requires an immutable CI
release manifest and an SSH resource target; omission is a hard configuration error.

Core execution boundary:

```python
await AnalysisExecutionService.create_run(
    tenant_id=..., actor_id=..., project_id=..., snapshot_id=...,
    idempotency_key=..., execution_mode="fixed" | "agent",
    base_run_id=... | None,
)
await service.list_runs(tenant_id=..., actor_id=..., project_id=...)
await service.matrix(tenant_id=..., actor_id=..., run_id=...)
await service.events(tenant_id=..., actor_id=..., run_id=..., after=0)
await service.cancel(tenant_id=..., actor_id=..., run_id=...)
await service.submit_inputs(
    tenant_id=..., actor_id=..., run_id=...,
    submissions=(InputSubmission(...),),
)
await service.execute(claim)

await review_service.create_cell_revision(..., expected_revision=0 | current_revision)
await review_service.review_cell_revision(..., revision=..., content_hash=...)
await review_service.create_export(..., mode="draft" | "reviewed")
await review_service.list_artifacts(...)
await review_service.approve_artifact(...)
await review_service.download_artifact(...)
```

Fixed mode freezes `analysis.execution.v1` / `analysis.fixed.v1`; Agent mode freezes
`analysis.execution.v2` / `analysis.agent.v1`. Both freeze route/model identity,
channel hash, streaming/reasoning settings, timeouts, input/output bounds and
`max_dispatches=2`; Agent alone permits `max_tool_rounds=1`. Migration `0040` owns
`analysis_runs`, `analysis_units`, `analysis_actions`, `analysis_cell_revisions`
and `analysis_events`; `0041` owns `analysis_input_requests` and
`analysis_tool_executions`; `0039` owns project/source snapshot tables. Migration
`0042` extends cell revisions and owns `analysis_reviews`, `analysis_artifacts` and
`analysis_approvals`; `0043` owns `analysis_dependencies`, base-run lineage, unit
freshness/invalidation reasons and reused revision lineage.

## 3. Contracts

### Source preparation and authorization

- The project creator must remain an active tenant member. Every project, snapshot,
  run, matrix, event and source-block read rechecks visibility of all bound sources
  with the actor ID. Missing and unauthorized data share `analysis_not_found`.
- A draft supports at most 3 objects, 10 fields and 10 files. A source version belongs
  to exactly one object or the shared-requirements group. Shared requirements define
  comparison criteria and never prove that a supplier satisfies them.
- PATCH and source confirmation require a quoted `If-Match` revision. Confirmation
  freezes an immutable manifest and is idempotent for the same project revision.
- The manifest binds source version, ingestion generation, source hash, structure
  hash, parser generation and coverage. Tenant-scoped foreign keys and RESTRICT
  deletes prevent a source identity from being moved or removed under a snapshot.
- `document_structures` stores one typed immutable JSONB payload per generation.
  Chunk offsets refer to its canonical text and are revalidated during ingestion
  recovery. Independent `document_blocks` / `chunk_block_spans` tables do not exist.
- Parser 2 preserves DOCX body/table order, including nested tables, and XLSX sheet,
  A1/range, merged cells, grouped hidden columns, formula strings/cache and number
  format. It never executes formulas. PDF provides text/page locators without a
  reliable layout-completeness or bounding-box claim. OCR is not implemented.

### Fixed and bounded Agent execution

- Run creation locks Tenant and validates the current project revision, complete
  source authorization, frozen route policy, daily generation budget and provider
  allowance. In one database transaction it creates the run and all object/field
  units, reserves one `analysis_cell` per unit, creates Job/Outbox records and appends
  the initial event. Any quota failure rolls the whole run back.
- `(tenant_id, project_id, idempotency_key)` is unique. A same-key/same-snapshot/mode
  retry returns the existing run; changing the snapshot or execution mode yields
  `analysis_idempotency_conflict`. Retries do not reset deadlines, dispatch counts or
  reservations.
- A unit retrieves one bounded evidence set from only that comparison object's
  frozen sources. Search coverage and parser coverage remain distinct. A bounded miss
  is `insufficient`, not proof that the complete document lacks the fact.
- Fixed mode accepts only `final`, `abstain` or `need_input`. Agent mode may use
  `tools` in slot one with at most three `search_sources`, `read_source` or
  `calculate` requests. Slot two is terminal and cannot open another tool round.
  Search/read remain inside the frozen object sources. Calculation uses Decimal,
  allowlisted operations, explicit units, rounding and provenance; it never evals
  model text or supplies missing tax, currency, quantity or period assumptions.
- `need_input` and a calculation with missing declared operands persist a revisioned
  input request, leave the reservation held and release the current Job lease. An
  authenticated submission must match request ID, tenant/run/unit, revision and the
  exact parameter set. It creates one idempotent resume Job, keeps the original run
  and unit deadlines and consumes only the remaining model slot. Replaying identical
  values is a no-op; changed/stale values conflict.
- The gateway performs one HTTP dispatch and has transport retries disabled. The
  durable executor owns the only retry decision. A pre-response connect failure or
  explicit 429/502/503/504 may consume the second slot on the same route within the
  original deadline. A sent timeout, partial stream, process-loss window, invalid
  schema, semantic validation failure, 401/403 or other rejection is not retried.
- `ProviderCallService.scope(..., intent_link=...)` inserts the provider dispatch
  intent and matching `analysis_actions` row in the same transaction before network
  I/O. The action's composite foreign key binds tenant, unit operation and dispatch.
  A committed intent without a durable result becomes `unknown`; checkpoint or Job
  replay must not send it again.
- Every state mutation rechecks Tenant -> run advisory lock -> Job lease/fencing ->
  run/unit -> authorization/reservation before dispatch. A changed frozen route,
  expired deadline, cancellation, lost lease or revoked source prevents new calls.
- A validated result and business settlement share one transaction. `succeeded`
  consumes one `analysis_cell`; failed/cancelled/expired units release it;
  `waiting_input` keeps it. Terminal replay is a no-op. Matrix/event reads and page
  refreshes never settle or dispatch.
- Run status is an aggregate of unit states. `completed` means every unit succeeded;
  `partial` means success plus failures; `waiting_input` means no runnable work and at
  least one pending input. It is not a review or publication state.

### Conservative invalidation and cross-snapshot reuse

- An incremental run explicitly binds `base_run_id`. The base must belong to the same
  project, use the same execution mode, be terminal and remain fully authorized. The
  idempotency key also binds the base run; replay cannot silently change scope.
- Every unit stores ordered fingerprints for object definition, execution policy, the
  field and its transitive field definitions, referenced parameters, upstream cell
  content, that object's complete frozen source scope and the common-source scope.
- A source-scope change invalidates every dependent field for that object, including a
  prior bounded miss with no citation. A common-source change invalidates all units.
  Field, parameter, object, policy and upstream changes expose stable typed reasons.
- Reuse requires an unchanged dependency fingerprint, `succeeded` base status, current
  freshness, the latest revision with a valid stored schema/content hash and a valid
  evidence receipt. Missing or tampered dependency history, result or evidence forces
  recomputation. Reused units create neither a Job nor a provider dispatch and do not
  consume another `analysis_cell`.
- Reuse copies the immutable result with explicit unit/revision lineage. It does not copy
  cell review or artifact approval. The new run must be reviewed and delivered as a new
  matrix revision. A later human revision marks transitive downstream units in that run
  stale without rewriting earlier artifacts.

The executor is currently an explicit durable state machine in
`AnalysisExecutionService`; no LangGraph checkpoint is used by this slice. This is
an implementation narrowing of the broader design, not a claim that LangGraph has
been integrated.

### Events, compatibility and resource boundary

- SSE returns a finite authenticated batch after `Last-Event-ID`, includes only event
  sequence/type/unit/code, and sets `no-store`. Matrix GET is authoritative after a
  missed event. The current React client polls GET and does not consume SSE yet.
- Parser isolation uses a managed child process, pipe I/O and a parent watchdog.
  Windows psycopg uses a Selector event loop, so process startup runs through a
  thread. Cancellation during startup must await startup and reap the child.
- Enable `0038` through `0043` in order before either feature flag. Drain upload sessions
  before parser 2 admission. Disabling flags stops new project/snapshot or run admission
  while historical reads and already admitted compatible jobs remain supported.
- Downgrades reject document/source history. `0040` also rejects downgrade when an
  analysis run or `analysis_cell` quota exists; `0041` rejects downgrade when an
  input request or tool receipt exists; `0042` rejects downgrade when review or
  artifact history exists; `0043` rejects downgrade when dependency, reuse or stale
  history exists. Do not deploy an older binary that
  cannot read parser 2 or analysis records after history is created.

### A5 evaluation and capacity evidence

- Holdout execution binds the pack manifest, upload inventory and reference identity
  before either arm runs. The reference file never enters model input. The scorer keeps
  the full 30-row denominator, including failures and not-run rows; unless every row is
  independently reviewed it returns `blocked_reference_unreviewed` with no quality
  score. Metrics include substantive revision rows, serious errors, missing/conflict
  preservation, latency, dispatches, known/unknown tokens and known/unknown cost.
- The frozen 4C4G plan contains two repetitions of ramp, steady-state, burst and
  recovery: 14 analysis runs, 12 parse uploads and at most 168 model dispatches. Cancel
  and idempotency replay are part of the workload. These numbers are a workload bound,
  not authorization to run or a capacity result.
- An external capacity report binds repository, ref, run ID, run attempt, workflow ref,
  deployed commit and immutable API/worker/consumer/web digests. The generic evidence
  validator reopens the hash-verified release manifest and resource JSON rather than
  trusting report summaries. Missing, blank, non-string or mismatched CI identity is
  rejected.
- Every runner API request carries one execution correlation marker. SSH snapshots must
  cover the workload on one 4 CPU/about 4 GiB node, show ready single-replica workloads
  without OOM, bind the same API Pod UID and show matching log observations monotonically
  increasing from zero to the report's complete HTTP request count. A runner pass without
  these source artifacts cannot become production-capacity evidence.

### Human review and artifact delivery

- A human edit never overwrites the model row. It creates the next immutable cell
  revision with `author_type=user`, `value_origin=user_provided`, no fabricated
  evidence and an exact supersedes link. A terminal failed/cancelled/expired unit
  with no model revision may start at `If-Match: "0"`; active units cannot.
- A review binds the current revision number and content hash. Any later revision
  makes the old review inapplicable. Reviewed export requires every current cell
  revision to have an approved review; draft export preserves failures and no-result
  cells in the complete denominator.
- Each export freezes an ordered matrix manifest, source manifest hash and exact cell
  revision IDs. The object key includes matrix revision and matrix hash, so two
  idempotency keys for identical content cannot overwrite one another.
- XLSX contains `Comparison`, `Pending Items` and `Sources & Basis`. It stores static
  values only, reopens for verification, rejects formulas and prefixes dangerous
  spreadsheet strings with an apostrophe.
- Export replay uses the same `(run, idempotency key)` row. If object storage succeeds
  but final DB confirmation rolls back, the row remains `writing`; replay verifies and
  finalizes the same object. Store/protocol failure marks the row failed.
- Reviewed artifact approval binds artifact hash, source manifest hash, matrix hash,
  operation and expiry. Download rechecks all source permissions before and after
  object metadata verification, then issues a short-lived URL. Previously downloaded
  bytes cannot be recalled.

## 4. Validation & Error Matrix

| Condition | Stable outcome |
|---|---|
| New run while execution flag/policy is unavailable | `analysis_execution_disabled` (409) / `analysis_policy_unconfigured` (409) |
| Missing or unauthorized project, run, snapshot or source | `analysis_not_found` (404) |
| Missing/stale quoted project revision | 428 / `analysis_revision_conflict` (409) |
| Invalid or reused key with a different snapshot | `analysis_idempotency_invalid` / `analysis_idempotency_conflict` (409) |
| Missing/foreign input request | `analysis_input_not_found` (404) |
| Stale revision, changed replay or wrong parameter set | `analysis_input_revision_conflict` (409) |
| Closed, busy or expired input request | `analysis_input_closed` / `analysis_input_busy` / `analysis_expired` (409) |
| Missing `analysis_cell` row / exhausted finite quota | `usage_product_quota_unconfigured` (409) / `usage_limit_reached` (429) |
| Shared generation / provider daily / unit allowance exhausted | `provider_generation_budget_exhausted` / `provider_daily_budget_exhausted` / `provider_operation_budget_exhausted` (429) |
| Invalid event cursor | `analysis_event_cursor_invalid` (422) |
| Source, structure, quote or stored result hash/range mismatch | `analysis_source_invalid` / `analysis_result_invalid`; no result is published |
| Frozen route differs at execution time | `analysis_route_changed`; no dispatch |
| Connect or explicit busy response with a slot left | Same primary route, at most one retry |
| Sent timeout, partial stream or unacknowledged dispatch | `analysis_response_unknown`; action remains unknown, no automatic resend |
| Provider rejects or output fails schema/evidence checks | `analysis_provider_rejected` / `analysis_output_invalid`; release the unit quota |
| Tool leaves frozen scope, has invalid locator/unit/evidence or corrupt receipt | typed `analysis_tool_*` / `analysis_calculation_*`; no unverified result is published |
| Slot two asks for another tool round | `analysis_terminal_action_required`; no third dispatch |
| Cancel, expired deadline or lost Job lease | No new dispatch; late response cannot update the matrix |
| Base run changes under the same idempotency key, is foreign, nonterminal or mode-mismatched | `analysis_idempotency_conflict` / `analysis_base_run_invalid`; no run is created |
| Dependency history, result schema/hash or evidence receipt is missing/tampered | Recompute with `dependency_history_invalid` / `previous_result_invalid`; never reuse silently |
| Cell revision omits/stales `If-Match`, or uses zero on a nonterminal/existing result | 428/422 or `analysis_revision_conflict` / `analysis_result_required` |
| Review targets a stale revision/hash or an already reviewed revision | `analysis_review_revision_conflict` / `analysis_review_conflict` |
| Reviewed export has an unapproved current revision | `analysis_review_required` |
| Artifact hashes, stored metadata or frozen manifest differ | `analysis_artifact_invalid`; no download URL |
| Approval target differs, is expired or already decided | `analysis_approval_target_conflict` / `analysis_approval_expired` / `analysis_approval_conflict` |
| Any bound source access is revoked before download | `analysis_not_found`; no new signed URL |
| Holdout reference is not independently reviewed for all 30 rows | `blocked_reference_unreviewed`; no quality pass or delta |
| Release repository/ref/run identity is missing, blank, non-string or differs between report and manifest | Evidence validation fails; no external 4C4G pass |
| Resource samples omit the runner marker, use another API Pod, start above zero or end below all HTTP requests | Evidence validation fails as incomplete target binding |

All API errors use the repository envelope and request ID. SSE, logs and metrics must
not contain prompts, document bodies, model reasoning, endpoint credentials, object
keys or signed URLs.

## 5. Good / Base / Bad Cases

- Good: create a 3 object x 10 field run once, lose the HTTP acknowledgement, replay
  the same key/snapshot and receive the same run. Each unit has at most two linked
  dispatch rows and at most one consumed `analysis_cell`.
- Base: bounded evidence lacks a price. The unit succeeds with an explicit
  `insufficient` result and searched scope; it does not invent zero or mark the
  supplier noncompliant. A `need_input` result remains visible and holds its existing
  reservation until a valid resume submission or cancellation.
- Agent: a calculation requests explicit quantity and period, pauses without a
  Worker lease, resumes once, records `1200 * 10 * 12 = 144000.00 CNY`, and exposes
  its server receipt in the matrix. The deadline and two-dispatch budget do not reset.
- Bad: retry an unknown timeout because the cell has no result, use common
  requirements as supplier evidence, read another object's source, create a third
  dispatch to repair invalid JSON, or call the model after a lease/permission loss.
- Review: a model failure remains visible while a user creates revision 1 with
  `If-Match: "0"`; approving its exact hash permits a reviewed export. Reusing an old
  hash after another edit conflicts.
- Delivery: identical manifests exported under different keys have different object
  locations. A lost final DB commit is recovered with the same key and artifact;
  revoked source access prevents a later download URL.
- Update: changing one field question creates a new snapshot/run, reuses the nine
  fingerprint-identical cells without Job/model/billing work, recomputes the changed
  cell with `field_changed:<field_id>`, requires new reviews and leaves the old approved
  workbook readable from its original run.
- A5 good: plan-only reports 14 analysis runs, 12 parse uploads, 168 maximum dispatches
  and `production_capacity_approved=false`; a later external report is accepted only when
  the original release/resources artifacts reproduce its identities and traffic counts.
- A5 base: a model execution file may exist while reference review is incomplete; scoring
  remains blocked and preserves the complete denominator.
- A5 bad: manually write `verified` into a summary while CI identity fields are empty or
  SSH samples came from a different cluster/API Pod. The validator must reject it.

## 6. Tests Required

- `tests/analysis/test_structure_*` and `test_analysis_*integration.py` use real files,
  isolated PostgreSQL schemas and controlled storage/hash embeddings. Assert source
  identity, parser coverage, cancellation cleanup, ETag, cross-object/tenant denial,
  immutable hashes and migration model parity/history guards.
- `tests/analysis/test_analysis_execution_integration.py` uses the formal entitlement
  administration service and real PostgreSQL. Assert atomic run/unit/Job/reservation
  creation, same-key replay, quota rollback, intent/action linkage, retry classes,
  unknown recovery, lease takeover, cancellation, late-response fencing, settlement
  and source-permission revocation.
- `tests/analysis/test_execution_api_integration.py` asserts authenticated create,
  mode selection, input submission/replay/conflict, list/status/matrix, cancellation
  and finite `Last-Event-ID` replay through ASGI.
- Web analysis tests assert a complete matrix denominator, per-unit failures, source
  citations, explicit modes, input resume, first manual revision after model failure,
  exact review and artifact refresh recovery.
- `test_analysis_review_delivery.py` uses real PostgreSQL and real XLSX generation.
  Assert immutable revision history, zero-version terminal recovery, stale review
  rejection, unique object locations, static workbook verification, write/DB rollback
  replay, exact approval and permission-rechecked download.
- `test_analysis_invalidation.py` uses real PostgreSQL and controlled HTTP. Assert
  unchanged reuse without Job/dispatch/settlement, parameter/upstream invalidation,
  review noninheritance, downstream stale propagation and refusal to reuse tampered
  dependency/result/evidence history.
- `test_document_structure.py`, `test_structured_upload.py` and
  `test_a5_materials_contract.py` assert scan/OCR gaps, formula-cache gaps, hidden
  content, external-link warnings, file/cell/archive limits, corrupt archives, frozen
  public-source hashes, 30-row denominators and physical separation of reference files.
- `a3-comparison.json` records only controlled-provider engineering behavior for a
  same-snapshot/same-route/two-slot fixed-versus-Agent case. Real-model accuracy,
  human revision effort, external latency/cost and customer acceptance remain null
  or not verified. Browser/real-PG coverage may use controlled HTTP;
  it must say that it does not prove external model quality or interactive IdP login.
- `test_analysis_holdout_evaluation.py` asserts reference/upload separation, SHA-256
  identity, full denominators, unreviewed-reference blocking and quality/usage fields.
  `test_run_analysis_capacity.py`, `test_business_telemetry.py` and
  `test_validate_recovery_capacity_evidence.py` assert the 14/12/168 workload, immutable
  CI release identity, source resource recomputation and correlation-to-Pod traffic binding.
- The documentation contract must include this file and require `## Proven Examples`.
  Run Ruff, cross-package Mypy, Web lint/typecheck/tests/build and the non-integration
  suite after cross-layer changes.

## 7. Wrong vs Correct

Wrong: send HTTP, then insert an action row if the response arrives.

Correct: commit provider intent and `analysis_actions` linkage together through
`intent_link`, then send; classify the unresolved committed intent as unknown.

Wrong: accept input by parameter name alone, reset the deadline/dispatch count, or
create a new resume Job on every identical submission.

Correct: bind the submission to the authenticated run/request/revision and exact
parameter set, preserve the reservation/deadline/remaining slot, and replay the same
submission without another Job.

Wrong: enable analysis after only `0039`, or omit a quota and rely on a default zero.

Correct: apply through `0043`, explicitly configure `analysis_cell`, then enable both gates.
Omission preserves old entitlement/schema compatibility; explicit zero is denial.

Wrong: derive an artifact object key only from matrix content, or overwrite a failed
model cell while hiding its execution state.

Correct: include immutable matrix revision in each object key; preserve the unit failure
and attach a separately versioned, user-provided result that can be reviewed exactly.

Wrong: accept an external 4C4G report because its summary says `verified`, while its CI
run identity is empty or its SSH log samples cannot prove the runner traffic hit that Pod.

Correct: reopen the hash-bound release/resources artifacts, compare all CI identity and
image fields, then require the same correlation marker and API Pod UID with observations
from zero through the exact runner HTTP request count.

## Proven Examples

- `packages/core/src/enterprise_doc_core/analysis/execution.py`
- `packages/core/src/enterprise_doc_core/analysis/gateway.py`
- `packages/core/src/enterprise_doc_core/analysis/review.py`
- `packages/core/src/enterprise_doc_core/analysis/export.py`
- `packages/core/src/enterprise_doc_core/analysis/dependencies.py`
- `packages/core/src/enterprise_doc_core/documents/structure.py`
- `packages/core/src/enterprise_doc_core/billing/product_usage.py`
- `apps/api/src/enterprise_doc_api/analysis/runs_router.py`
- `apps/worker/src/enterprise_doc_worker/analysis_handler.py`
- `tests/analysis/test_analysis_execution_integration.py`
- `tests/analysis/test_analysis_review_delivery.py`
- `tests/analysis/test_analysis_invalidation.py`
- `tests/analysis/test_a5_materials_contract.py`
