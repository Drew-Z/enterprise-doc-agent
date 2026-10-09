# Presales workspace: current local implementation

Implemented by `09-12-saas-presales-workspace` on 2026-09-12 and extended with real
file ingestion by `09-12-saas-presales-ingestion` on 2026-09-13. This is a bounded
workflow within an existing enterprise session. Separate local tasks now provide
admission, browser OIDC, invitations and commercial request quotas. Payment,
automatic requirement extraction and production model/customer acceptance remain
outside this workflow. Commercial contracts are in [entitlements-usage.md](entitlements-usage.md).

## Persisted execution modes (2026-10-08 candidate)

### 1. Scope / trigger

User-selected `auto` / `deep` must survive admission, replay and Worker replacement.
This is local candidate behavior; real two-mode quality, latency and cost acceptance
remains open. Production rc.40 does not include this contract.

### 2. Signatures

Single-row POST accepts optional `GenerateRowInput`; batch accepts
`BatchGenerateInput.execution_mode`. Both complete and receipt responses retain
their prior status semantics. `PacketView.available_execution_modes` advertises
supported choices; `AttemptView.execution_policy` returns the saved policy.
Migration `20261008_0034` adds nullable object JSONB `presales_attempts.execution_policy`.
Downgrade locks the table and refuses when any policy history exists.

### 3. Contracts

New clients send `executionMode`; old bodyless or omitted-mode requests retain
the legacy path and a null policy. `presales.execution.v1` freezes mode, queue/row
timeouts, maximum provider requests, original daily dispatch cap, ordered routes,
model/version/revision, reasoning, streaming, per-route timeout, output size, and
endpoint/prompt SHA. Never store credentials or endpoint text in this snapshot.
Auto uses accepted operational settings. Deep requires background execution,
raises reasoning below high to high (preserves xhigh), allows 300 seconds per
route and at least 660 seconds per row within the 900-second cap. It preserves
the existing one/two-dispatch and output-size limits.

First start computes the execution deadline from the saved row budget. Recovery
keeps that deadline and the existing call ledger. Restore private gateways from
the saved routes using current credentials for the same endpoint and prompt only.
Enforce the smaller of saved/current daily caps. Worker progress timeout covers
the maximum persisted row budget, not just the new process default.

### 4. Validation and errors

| Condition | Result |
|---|---|
| Invalid mode or client-supplied budget | 422; no attempt/reservation |
| Same key with changed or omitted original mode | 409 idempotency conflict |
| Deep on synchronous service | 409 background required |
| Missing required route during admission | 503 policy unavailable; no admission |
| Missing route, changed endpoint or prompt during execution | Failed policy unavailable; no provider dispatch, reservation released |
| Daily limit already used | Dispatch budget failure; ledger is not reset |

### 5. Good / base / bad cases

Good: an interrupted deep task resumes its original fallback/model/deadline even
when process defaults change. Base: old null-policy rows remain readable. Bad:
recompute a policy at replay or silently execute it with a new prompt or endpoint.

### 6. Tests and assertion points

`test_presales_execution_policy_integration.py` exercises real API/PostgreSQL and
controlled HTTP: sync auto, deep admission/replay, interrupted two-route recovery,
original/current daily caps, rejected requests, batch per-row conflicts, endpoint
and historical-prompt drift, additive migration and history-preserving downgrade.
Assert exact provider counts, original deadline/policy and a single settlement.
The background and ordinary browser suites independently cover new/legacy flows.

### 7. Wrong versus correct

Wrong: mutate the shared gateway or extend the deadline after a restart.
Correct: restore a private gateway from the durable policy, preserve ledger/deadline,
and fail explicitly when the original identity cannot be served.

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
| POST /api/presales/{id}/generate | Background only: 1–12 distinct rowIds, per-row derived keys; returns packet and rejected rowId/code pairs |
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

`authorize_principal(..., lock=True)` takes `FOR NO KEY UPDATE OF tenants`
(`with_for_update(of=Tenant, key_share=True)`). This still serializes admissions,
quota/configuration changes and tenant deactivation, while allowing the KEY SHARE
locks used by unrelated job/audit foreign-key inserts. Presales does not modify
the tenant key. Using FOR UPDATE here unnecessarily blocks behind those inserts;
removing the lock would instead break admission serialization. Packet/row locks,
source checks and response-time authorization remain unchanged.
`test_presales_lock_integration.py` holds a real unrelated audit insert open and
requires single/batch HTTP admission and replay to complete with one Job and
reservation. A second test observes `pg_blocking_pids` during tenant deactivation,
then requires 403 and no admission after commit. Keep the concurrent last-slot
test in `test_presales_admission_integration.py`; local lock tests do not prove
the deployed latency or capacity targets.

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

The default synchronous path runs one bounded row at a time in the API. Claim an
attempt in a short tenant/row transaction, release it for retrieval/network I/O,
then reauthorize and fence by attempt state/deadline before saving the draft.
An expired execution cannot overwrite a newer attempt. Same-key replay does not
call the provider again. Default limits: 3 attempts per row, 2 live attempts per
tenant, 100 attempts per UTC day; failures count toward these development budgets.
The optional background path below uses the existing Job runtime and has a separate
queue capacity; both feature switches remain false by default.

`ApiSettings.presales.generation_enabled` defaults to false. Enabling generation
requires an OpenAI-compatible selected model configuration; deterministic mode
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
invalid JSON/schema or citations fail the row. The gateway itself never repairs,
retries or switches routes. The background coordinator alone can recover eligible
transport failures within its persistent dispatch budget.

## Explicit route and timeout contract

This is the synchronous/default contract with automatic recovery disabled. The
background section defines the additional opt-in coordinator behavior.

1. **Scope:** choose a proven configured route for Presales without changing the Agent
   gateway or automatically issuing another potentially billable request.
2. **Signature:** `OpenAICompatiblePresalesGateway(model, presales_settings=...)`, wired
   by the production API factory. The generate HTTP API and attempt schema are unchanged.
3. **Environment:** `PRESALES__MODEL_ROUTE=primary|fallback` (default primary), optional
   `PRESALES__MODEL_TIMEOUT_SECONDS` (>0, less than `PRESALES__ROW_TIMEOUT_SECONDS`,
   maximum row budget 180). The override sets both the HTTP timeout and total model
   deadline. If absent, preserve the selected route's existing timeout. Fallback
   selection uses the existing `MODEL__FALLBACK_*` credentials/name/version; it never
   copies primary revision metadata. Keep `PRESALES__GENERATION_ENABLED` explicit.
4. **Errors:** unknown route, missing selected fallback configuration or invalid wait
   budget rejects configuration. Runtime timeout records `presales_model_timeout` with
   one observed dispatch, without another provider request. The Web nginx `/api/`
   proxy waits 210s, covering the bounded row execution plus result handling, and
   returns one request ID even for proxy-generated errors.
5. **Cases:** default preserves the primary route; explicit fallback sends only to
   that route; either route timing out must not trigger the other. A browser/proxy
   disconnect and an upstream completed/billed record can coexist.
6. **Tests:** inspect actual HTTP boundary URL, credentials, model, timeout and call
   count for both selections; reject incomplete configuration; verify unchanged
   shared ModelSettings and selected-route provenance. Exercise PostgreSQL generation
   and a real nginx upstream response delayed past the old 60s boundary.
7. **Wrong vs correct:** wrong: retry/fail over after an uncertain timeout because
   the UI has no draft. Correct: read the existing sheet, keep attempt history, and
   require an explicit new attempt. Provider billing is not inferred from local status;
   a receipt without response content cannot reconstruct an unsaved draft.

Statuses: supported, conditional, contradicted, insufficient_evidence,
conflicting_evidence. Conditional needs conditions; insufficient evidence needs
missingInformation; other statuses need citations; conflict needs two versions.
`validate_citations` binds exact server-resolved excerpts to authorized candidates. These checks
do not prove logical entailment or complete capture of contractual conditions.

## Controlled citation selection

### Focused public replay candidate (2026-10-09, deployed rc47)

Release/compatible rc46 rollback/reapply and workbook history passed; the deployed
SWU03 keyword query finds both required clauses first without provider dispatch.
Semantic quality remains open; see docs/ops/rc47-release-validation.json.

1. **Scope / trigger:** SWU03 showed zero keyword recall despite the clause existing in
   two stored chunks. PostgreSQL simple FTS represented its unspaced Chinese query as
   one whole token. Historical provider errors retain only an overall code, so their
   original rejection causes cannot be reconstructed.
2. **Signatures:** HybridRetrievalService keeps its public retrieve signature.
   When primary keyword recall is empty, Chinese queries use at most32 unique
   literal three-character windows (two for a two-character run), sampled across
   long input; require at least two matching windows when available. Existing
   authorized query, one roundtrip, vector call, RRF, top-k and source checks remain.
3. **Contracts:** PresalesError optionally carries an allowlisted OutputDiagnostic.
   Save only its fixed value in existing attempt provenance as
   providerCall1Diagnostic / providerCall2Diagnostic. Keep public error codes,
   accounting, route health, recovery eligibility, deadlines and stored call history.
   No migration, raw responses, quoted text, arbitrary error messages or credentials
   are added to product persistence. Prompt presales.v15 keeps the v14 basis schema
   and decoder while clarifying edition/entitlement/time scope and relevant citations.
4. **Validation / errors:** categories distinguish envelope_json, envelope_shape,
   incomplete_output, unsafe_response, draft_json, draft_schema, basis_quote,
   support_quote, support_combination, draft_contract, citation and stream_contract.
   Classification never turns an invalid response into a draft. Unknown/malformed
   diagnostics are not accepted as arbitrary strings.
5. **Good / base / bad:** good: a Chinese requirement finds its literal certificate
   clause without a vector match; base: non-Chinese fallback and exact primary matches
   retain existing behavior. Bad: infer compliance from lexical overlap, relabel
   unmet by keyword, or claim a commercial trial disproves education entitlement.
6. **Tests:** test_public_replay_remediation_integration.py checks real PostgreSQL
   recall, inactive-generation/tenant rejection, synchronous diagnostics and durable
   failure/recovery/replay. test_presales_output_diagnostics.py checks HTTP envelopes,
   quote/support errors, one dispatch, observed usage and absence of sentinel bodies.
   Existing literal-basis and public workflow tests continue unchanged semantically.
7. **Wrong vs correct:** diagnostic visibility is not proof failure rate improved;
   stronger scope instructions are not an entailment guarantee. Keep original failures,
   separate controlled tests from live diagnostics, and do not alter old scorer schemas.
   New policies freeze the v15 SHA; drain accepted old policies before any deployment.

1. **Scope:** `presales.v3` replaces model-transcribed quotes with request-local
   references. It does not adopt the rejected v2 classification prompt. Saved
   drafts, public API fields, reviews, CSV, schema and existing rows are unchanged.
2. **Signatures:** `prepare_citations(GenerationInput) -> (SelectionInput, catalog)`
   and `resolve_selection(content, catalog) -> ModelDraft` live in
   `presales/citation_selection.py`; only the HTTP gateway uses `SelectionDraft`.
3. **Contracts:** add `citationId` to each model-facing evidence fragment. Model
   citations contain only `{"citationId": "cite_<request-prefix>_<number>"}`.
   The call-local catalog holds the original chunk/version/text. Split at most
   twelve 1800-character candidates into ordered substrings of at most 600
   characters, preferring punctuation/whitespace boundaries in the latter half
   of a window. Trim only edge whitespace; do not rewrite punctuation or omit
   non-whitespace characters. Both original input and final request remain
   bounded by 128 KiB. At most twelve selections may be returned.
4. **Errors:** unknown, duplicate or other-request references produce
   `presales_invalid_citation` with one observed dispatch. Legacy excerpt/UUID
   fields, invalid statuses or one-version conflicts produce
   `presales_invalid_model_output`. Unselected versions, invalid UUIDs or duplicate
   candidates fail before dispatch as `presales_invalid_evidence`; excessive
   input fails as `presales_input_too_large`. None triggers repair or retry.
5. **Cases:** good: select two authorized versions and persist both exact quotes.
   Base: evidence shortage still permits an uncited `insufficient_evidence` result
   with follow-up questions. Bad: accepting a valid-looking reference from another
   simultaneous request, or treating two fragments of one version as a conflict.
6. **Tests:** `test_presales_citation_selection.py` exercises the real gateway HTTP
   boundary, long multilingual sources, immutable inputs and concurrency.
   `test_presales_workflow_integration.py` uses real PostgreSQL and retrieval to
   verify persistence/review/export, idempotency, revocation and stale snapshots.
   Both browser harnesses emit references through their controlled HTTP model.
7. **Wrong vs correct:** wrong: tolerate changed punctuation or search all documents
   for an approximate quote. Correct: resolve only this request's offered fragment,
   then retain the existing tenant/version/substring and commit-time authorization
   checks. Source-backed text alone does not prove that the answer is correct.

## Prerequisite assessment and generated language

1. **Scope:** `presales.v4` addresses capability being confused with current order
   readiness, and wholly English business prose. It extends only `SelectionDraft`;
   existing `ModelDraft`, stored drafts, human reviews and CSV remain compatible.
2. **Signatures:** `Prerequisite(condition: TextItem, state: met|unmet|unknown,
   citations: list[CitationReference])`; `SelectionDraft.prerequisites` is required
   (0–12 items), each prerequisite selects 1–12 offered references. No relevant
   prerequisite means an explicit empty list, never an omitted field.
3. **Contracts:** `supported` forbids unmet/unknown prerequisites. `conditional`
   retains each outstanding condition in public `conditions` (v4/v5 required duplicate
   model text; v7 projects it as described below). Resolve the ordered union of conclusion and prerequisite
   selections; shared references across these positions are materialized once.
   Do not require the model to repeat prerequisite references at top level.
   The final public draft still has at most 12 exact citations.
4. **Errors:** missing prerequisite assessment, internal classification/condition
   inconsistency, duplicates within a prerequisite, or wholly non-Chinese answer,
   condition or follow-up prose -> `presales_invalid_model_output`. Unknown IDs at
   either selection location -> `presales_invalid_citation`. One observed request,
   no repair, translation, classification rewriting or extra model call.
5. **Cases:** good: an explicitly purchased/accepted module can be supported;
   base: a module awaiting validation is conditional with the validation step;
   bad: calling optional capability enabled without purchase/completion evidence.
   English technical names and original citations remain valid within Chinese
   business prose. A Han-character presence check only detects wholly non-Chinese
   text; it is not complete language identification or factual verification.
6. **Tests:** HTTP-boundary regressions cover outstanding vs met prerequisites,
   omitted prerequisite, reference union and foreign IDs, wholly English fields,
   Chinese text with SAML/product names and intact English citations. Existing
   PostgreSQL/browser tests retain persistence, reviews, export and revocation.
7. **Wrong vs correct:** wrong: keyword-match a contract to rewrite its conclusion,
   or claim the assessment proves no facts were omitted. Correct: check internal
   consistency and exact source identity, then evaluate semantic correctness with
   frozen data. Preserve failed original outputs separately from decoder replay.

## Decision precedence and actionable conditions

### Single business proposition (v12 candidate)

`PropositionDraft` removes the second model-written prerequisite `condition`.
The server validates the proposition and support through the existing evidence
resolver before projecting `核验事项：` plus the exact proposition. The 995-character
input bound reserves five characters within the public 1000-character limit.
Chinese-language validation applies before adding the prefix, so English-only
propositions cannot pass merely because the server contributes Chinese text.

Keep the description neutral: reviewers can change state but cannot rewrite the
original prerequisite text. Embedding an immutable state label in that text would
contradict a later correction and duplicate CSV labels. Public state, citation
indexes, draft persistence and review-history contracts remain unchanged.

`presales.v12` and `presales-gateway-run-v5` select the new protocol; historical v4
reports still decode model-written conditions with `resolve_evidence_selection`.
Old `condition` fields are rejected by the new provider schema, never silently
ignored. No added inference, keyword relabeling, or promise of semantic entailment.
Examples: `test_presales_proposition_selection.py`, the v4/v5 scoring regression,
and the real workflow integration's state-correction/CSV case.

### Separate definitions and missing records (v14 candidate)

The private `BasisDraft` schema replaces each prerequisite's context IDs with
`definition` (1–12 literal quotes) and `unconfirmed` (0–12 literal quotes), alongside
the unchanged proposition, positive/negative support and uncertainty. Every quote
uses a current-call citation ID and 1–600 characters from that exact fragment.
Definitions explain why the obligation applies; unconfirmed records describe a
gap in knowledge. They do not establish noncompletion. Distinct definition quotes
may share a fragment; the public citation union retains each ID once in order.

`unconfirmed` requires `uncertainty=missing`. Any missing or conflicting assessment
requires nonempty Chinese `missingInformation`. Existing support-combination,
language and total-status checks still run through the historical proposition
resolver. Empty definitions, unsupported definition/gap quotes, foreign proof IDs,
and legacy per-item citations fail as `presales_invalid_model_output`; top-level
foreign citation IDs retain `presales_invalid_citation`. Rejection preserves safe
request/response IDs and observed usage, with no repair or extra HTTP dispatch.

`presales.v14` and `presales-gateway-run-v6` use `resolve_basis`. Both offline scorers
select it explicitly for v6; v1–v5 keep their original decoders and failure records.
Public drafts, saved JSONB, revision history and CSV schemas do not change. No DB
migration, route change or budget increase is required. The prompt and schema match
the successful one-call v14 candidate; this is a known regression, not full quality
or independent business approval. Literal source binding cannot prove semantic
entailment, and the server never relabels state using words in the evidence.

Good: a training completion obligation plus an unupdated training status gives
unknown and a confirmation question. A literal failure gives unmet. A separate
obligation to submit a certificate can be unmet even when training is complete.
Bad: treat a missing training record as proof training did not happen, or cite the
order alone without an enabling rule. Public HTTP cases live in
`test_presales_basis_selection.py`; historical scorers, actual PostgreSQL workflow
and 1440/390px browser journeys retain draft/review/export coverage. All local model
HTTP responses are controlled; full deployed quality and capacity remain separate.

### Literal prerequisite evidence (v11 candidate)

The rc.34 deployed gateway uses the private `EvidenceDraft` provider schema. Every
prerequisite supplies a positive business proposition, `uncertainty`, positive and
negative quote arrays, a Chinese condition and context citation IDs. `none`
requires exactly one supported direction; `missing` requires neither; `conflict`
requires both. The server derives met/unmet/unknown from these combinations.
Model-supplied `state` is rejected, with no fallback to the older provider schema.

Each quote contains this call's `citationId` and a nonempty literal `text` of at
most 600 characters. It must occur within that exact catalog fragment, never just
another authorized source. Invalid support is `presales_invalid_model_output`;
unknown context/final citation IDs retain `presales_invalid_citation`. The original
gateway envelope guards, usage and safe response/request IDs remain in force.
There is one HTTP dispatch per gateway call; background recovery policy is unchanged.

`resolve_evidence_selection(content, catalog)` unions explicitly selected support
and context references, then reuses `SelectionDraft` and `resolve_selection` for
language/status/conditions and public projection. Public drafts, saved JSONB,
review history and CSV keep their existing schema. Literal containment does not
prove semantic entailment: missing-record versus negative-fact interpretation,
source precedence and final business approval still need semantic evaluation.

New evaluation reports use `presales-gateway-run-v4`. Offline scorers select the
new parser only for v4; v1/v2/v3 retain their historical interpretation and frozen
failure denominator. Never relabel an old failed output with the new decoder.

Good: purchase has positive text, unfinished configuration has negative text,
unrecorded acceptance has neither and remains unknown. Bad: fabricate a negative
quote, accept a previous call's ID, or infer state by keyword rewriting. Regression
examples: `test_presales_evidence_selection.py`, `test_presales_citation_selection.py`,
`test_presales_gateway_score.py` and `test_presales_workflow_integration.py`.
This contract was deployed in rc.34 with verified package/configuration identity;
postdeployment business/semantic acceptance remains separate from rollout health.

`PresalesSettings.primary_reasoning_effort` (low/medium/high/xhigh or null) and
`primary_streaming` (boolean or null) override only the primary presales gateway.
Null inherits the shared ModelSettings value; explicit false disables streaming
even if shared streaming is true. The adapter copies settings, never mutating the
shared Agent configuration. Selecting fallback ignores both primary overrides and
retains that route's own settings. Worker route construction preserves the overrides
while changing only model_route. Existing per-route and total row deadlines apply.

The staging renderer/CLI and workflow carry `PRESALES__PRIMARY_REASONING_EFFORT` and
`PRESALES__PRIMARY_STREAMING`. Omitted/empty arguments remove stale overrides; false
is preserved explicitly. ConfigMap and workload approval fingerprints cover both.
The schema 0032 `presales_inference` release mode combines these changes with approved
application images and complete rollback, without changing shared model settings.
See `test_presales_citation_selection.py`, `test_configure_staging_manifest.py`,
`test_release_switch.py` and `test_stream_background_integration.py` for examples.

`presales.v5` keeps the v4 model/public structures. The prompt defines an ordered
assessment: unresolved contradictions between applicable sources take precedence
over selecting one side's hard limit; after resolving source priority, an explicit
negative fact is contradicted; a missing proof is insufficient evidence; a proven
capability with an explicit enabling path is conditional; otherwise all requirements
and prerequisites must be proven for supported.

Absent reports/certificates do not prove nonexistence. A direct statement that a
required certification has not been obtained is different from an omitted attachment.
An explicit priority applies only to its stated subject/scope, not all provisions.
This is a model instruction, not a deterministic semantic guarantee. Do not use
keyword rewriting to make outputs agree with reference labels.

`SelectionDraft` additionally rejects `conflicting_evidence` without nonempty
`missingInformation` as `presales_invalid_model_output`, with one dispatch and no
repair/retry. Original `ModelDraft`, saved drafts and human review validation are
unchanged. HTTP boundary tests cover both accepted questions and missing-question
rejection with two valid source versions. Conflicting browser/DB fixtures must
include a clarification item so citation and authorization tests reach those checks.

Good: cite both applicable sides and request priority/scope clarification. Base:
request a missing certificate and its coverage/validity. Bad: use a prohibition's
stronger wording to silently override an equally applicable promise. Unmet/unknown
prerequisites use actionable Chinese wording, such as "需配置保留策略"; never present
them as already completed facts. Satisfied prerequisites remain out of the action list.

The rejected `presales.v6` prompt trial tried more explicit prose/source-relation and
unknown-state instructions, but still marked an unrecorded acceptance state unmet.
It also returned chunk UUIDs as citationId and mismatched duplicated condition text.
Its prompt was withdrawn back to v5; the deployment still uses v3. The v7 protocol
candidate below removes the duplicate fields instead of adopting the v6 prompt.
Retain the frozen v6 prompt and raw results as evidence, not active runtime behavior.
Do not repair UUID references or relax condition checks to relabel failed attempts.

Semantic review must distinguish evidence about completion from production eligibility:
missing records do not prove non-completion. A correct conflict label can accompany
incorrect prose about which source agrees with the requirement. Additional imperative
prompt text does not guarantee either distinction; test exact original responses.

## Single-source model protocol (v7 candidate)

1. **Scope:** the initial v7 change affected model-facing input/output only. The
   later prerequisite-review contract below also extends public drafts/reviews
   and CSV. v7 remains a candidate until the separate live semantic/release gate passes.
2. **Input:** `SelectionInput(requirement, evidence)` explicitly projects each fragment
   to `citationId`, exact `text`, `source: {label, filename, applicability,
   versionNumber, latestVersionNumber}`, `heading`, `pageNumber`. Source information
   comes from the authorized snapshot, not copied arbitrary evidence metadata.
   Display labels distinguish source versions even with identical filenames; they
   are not selectable citation IDs. Internal UUIDs/hashes remain on the server.
3. **Output:** `SelectionDraft` requires structured prerequisites, never a `conditions`
   field. `resolve_selection` projects conditions from every unmet/unknown prerequisite,
   preserving text/order and removing exact duplicate text. Met items stay out of the
   action list. All statuses retain outstanding prerequisites; supported forbids them
   and conditional requires at least one. Unknown is not converted to unmet.
4. **Errors:** old duplicate `conditions` fields are extra-field errors. Conditional
   without outstanding prerequisites, English-only prerequisite text (including met),
   invalid state or missing questions remain invalid output. Internal UUIDs are not
   aliases for citation IDs. Duplicate/foreign/cross-request IDs, same-version conflict,
   tenant/generation/substrings and commit-time authorization remain enforced.
5. **Cases:** a purchased module plus unfinished configuration and unrecorded acceptance
   becomes two pending conditions, with all explicitly selected evidence retained.
   An old version remains identifiable through its version/latest-version metadata.
   Wrong: accept a chunk UUID or rewrite condition prose after failure. Correct: define
   the projection before inference and reject outputs outside the new contract.
6. **Validation:** real HTTP adapter tests exercise input minimization and condition
   projection; PostgreSQL tests check persistence, unchanged original drafts, reviewed
   vs draft CSV, idempotency and revocation. Both browser harnesses consume SelectionInput
   and produce SelectionDraft. These controlled tests do not prove model semantics.

Attempts store model provider/name, pipeline and prompt versions, prompt SHA,
configured model version/revision and returned model/response ID when available.
Configured or returned identifiers do not authenticate upstream model weights.
Index generation IDs are in source snapshots. Deployment commit/image identity
is recorded by release evidence, not inferred from a dirty working tree.

### Fact-first prompt candidate (v8, not deployed)

The v8 candidate separates business completion from eligibility to enable a service.
It assesses prerequisite facts before writing the status and answer: explicit
satisfaction is met, explicit non-satisfaction is unmet, and absent completion
evidence is unknown. A completed check, a passed check and filing its report are
different predicates. Unknown asks to confirm the event and supply evidence; it
does not command completion as if non-completion were already known.

Only prompt text/version and model-facing schema property order change:
SelectionDraft lists prerequisites before status, and Prerequisite lists state
before condition. Field names, validators, public data, stored results, request
limits, routes and retries are unchanged. Neither field order nor stronger wording
guarantees inference quality. Preserve original v7 failures, freeze the complete
v8 system message, and run separately authorized, bounded original-output trials
before considering release. The offline prerequisite-review scorer below the
quality spec complements classification/citation scoring; it does not replace
review of the answer, conflict direction or independent business approval.

### Scope-limited evidence (v9 candidate)

The deployed v8 prompt incorrectly rejected an explicit thirty-day retention fact
within an expressly synthetic test scope because the source was not customer data
or domain approval. v9 distinguishes scope from prerequisites: synthetic evidence
can support its stated test scope; it cannot prove a real customer/production claim.
Approval is assessed only when the requirement or evidence actually makes it a
prerequisite. Applicability cannot invent facts, cancel source conditions or instruct
a classification. Public schemas, citations, routes, budgets and stored drafts are
unchanged; there is no deterministic relabeling of generated output.

One original-output regression on the same saved excerpt returned supported with
the correct scope restriction, no invented prerequisites and an exact citation.
The original deployed insufficient_evidence result remains unchanged. This is a
known-failure regression, not a new blind benchmark or independent approval; do not
infer production-scope or explicit-approval negative-case performance from this sample.

providerRequestCount is an observed client dispatch count, not a remote execution
or billing count. It is 0 before dispatch, temporarily NULL once dispatch is
prepared, and 0/1 once a synchronous outcome is observed (0–2 for background
operations). Preflight rejection remains 0; a background `not_sent` slot does not
count as a request. Interruption/crash leaves NULL while any call is unobserved.
A late expired execution under the current lease may update accounting
but cannot save a draft. Missing token usage remains null. None of these fields
constitutes a payment ledger. The separate commercial request ledger now reserves
in the attempt-creation transaction and settles with a successful validated draft;
failures/cancellation release quota while provider observations remain independent.

## Background generation resilience

This branch adds migration `20260924_0028`; it is not yet deployed. Defaults:
`background_generation_enabled=false`, `automatic_failover_enabled=false`,
`queue_timeout_seconds=900`, `queued_attempt_limit=24`, `daily_dispatch_limit=200`,
`route_failure_threshold=3`, `route_cooldown_seconds=30`. Automatic failover requires
background generation. API and Worker must use the same settings and model routes.

- **Admission:** one transaction creates the PresalesAttempt, `presales.generate`
  Job, job.created event and commercial reservation. No Celery Outbox entry is
  created. The API returns 202 when requested work is active; an already completed
  replay returns its saved packet without a new reservation. PacketView includes
  generationMode=synchronous|background so clients keep separate row requests when
  background is disabled; batch admission then rejects with 409
  presales_background_required before reserving or dispatching. Batch keys derive from
  the request key and row ID; a row rejection does not prevent remaining rows from
  being admitted, while shared authorization/source failures stop the batch.
  Enabled batch admission assembles the packet only after enqueueing; each row's
  transaction already checks tenant/member/source access. Do not add a complete
  pre-admission `get()` to repeat those reads. Disabled mode retains the initial
  authorized read, so access/source errors still precede the background-required
  error. `test_presales_admission_integration.py` compares equivalent one-row HTTP
  submissions: batch SELECT count must not exceed the single-row endpoint's count,
  with queued Jobs, one reservation per operation and no provider calls on replay.
  It also checks cross-tenant, revoked-member, unavailable/stale-source rejection
  without Jobs/reservations in both enabled and disabled modes. Final `get()` access
  rechecks remain mandatory for complete-content responses; query counts alone do not prove online latency.
  Single-row and equivalent batch admission have a 19-SELECT integration budget.
  The commercial ledger checks an operation once after acquiring the tenant lock;
  every reservation writer takes that lock, so waiting for the entitlement lock
  does not require another lookup. A newly inserted Job starts its event sequence
  at 1 without querying an empty history; subsequent events retain locked max+1.
  Daily and active counts use separate indexed scalar subqueries in one statement,
  preserving tenant locks, predicates and daily-limit error precedence. Reuse the
  commercial reservation receipt's stored expiry to clip queued work; do not reload
  that row or recompute expiry. Boundary tests cover yesterday's still-active work,
  today's failures, expired queue entries, concurrent last-slot admission, and
  rollback of Job/attempt/reservation when the TTL cannot cover execution.
- **Optional receipt:** both generate POSTs accept `response=receipt`; omitted or
  `full` retains the complete PacketView/BatchGenerateResult contract. A receipt
  contains only `packetId`, `admissions` (rowId/disposition/attemptId), and `rejected`.
  `enqueued` is emitted only after the existing Job/event/attempt/reservation
  transaction commits. `replayed` identifies the original key's attempt without
  claiming its current state; `already_drafted` has null attemptId and starts no work.
  Any new admission returns 202; only replays/existing drafts/rejections return 200.
  Disabled background mode rejects after an authorized read and never runs a model.
  Every row retains transaction authorization and source checks. A missing row is a
  batch rejection only after an error-path packet access check distinguishes it from
  an inaccessible packet. Shared access/source errors stop the batch; earlier commits
  remain recoverable. Clients must GET the sheet for content and current state, with
  the original final authorization/source checks. No migration or quota change.
  `test_presales_admission_receipt_integration.py` verifies 13 SELECTs versus the full
  contract's 19, a body under 300 bytes for one row, durable independent-connection
  visibility, same-key replay, partial rejection, rollback and later source revocation.
  Capacity plans opt in with `admission_response=receipt`; legacy plans default to
  full. New admission requires 202/enqueued and a matching attempt on the subsequent
  GET. Its duration is separate from `first_read_duration_ms` and total terminal
  duration; a failed read retains the already-observed admission timing and failure.
  These checks do not turn previous replay diagnostics into new-task latency evidence.
  Validation/error matrix (single-row / batch receipt):

  | Input or state | Single row | Batch |
  | --- | --- | --- |
  | New committed attempt | 202 / enqueued | 202 if any row enqueued |
  | Existing operation key | 200 / replayed, same attemptId | Same per-row disposition |
  | Existing draft, different key | 200 / already_drafted, null attemptId | Same per-row disposition |
  | Revoked member / inaccessible packet | 403 / 404 | Whole request 403 / 404 |
  | Source unavailable / stale | 404 / 409 | Whole request 404 / 409 |
  | Background disabled | 409 after access validation | Same |
  | Missing row in accessible packet | 404 | Per-row rejection, continue |
  | Reservation too short | 503, transaction rolled back | Per-row rejection, no admission |
  | Unknown response option | 422, no generation | Same |

  Good: accept a durable receipt, then GET and verify the actual attempt. Base:
  omit the option and retain the original full-content behavior. Bad: treat a
  replay receipt as a completed draft, return before commit, or count a rejected
  row as accepted. The main pitfall is accidentally keeping the full `get()` on
  the receipt path, or removing it from legacy/content reads. Public integration
  assertions and separate read timing protect both contracts.
- **Execution:** the existing Worker/publisher process runs one asynchronous
  presales poller. Long inference does not occupy the solo document consumer. Job
  leases, heartbeats, fencing and terminal projection are reused. Shutdown cancels
  local I/O and preserves the lease for recovery; it is not user cancellation.
  Generic dead-job retry rejects this job type, preventing a Celery/quotas bypass.
- **Deadlines and access:** queued work has an independent deadline, bounded by the
  commercial reservation expiry minus the execution budget. First claim fixes the
  execution deadline; restarts do not extend it. Admission, claim, dispatch and save
  reauthorize tenant, author and source versions. Final writes lock Job, Tenant,
  then domain rows. An old lease cannot overwrite the current draft. Cancellation,
  expiry, revoked access or terminal Job state releases any outstanding reservation,
  including for an inactive tenant; it does not reactivate the tenant.
- **Recovery:** before each HTTP request, persist one of at most two ProviderCall
  slots. Start with model_route, then the other configured route only when enabled.
  Never reuse a route label or endpoint/model hash within the operation. Recover only
  transport/timeouts, HTTP 408/426/429/5xx, or recognized 200 error envelopes. HTTP 426
  is `presales_model_upgrade_required`: try only the other route, never the same
  endpoint/model again in the operation. A dispatched `presales_invalid_model_output`
  rejection can use the remaining distinct route slot, including after lease recovery.
  Both responses still undergo the complete contract validation; no draft is repaired
  or saved on failure. Invalid citations and business/access failures remain terminal.
  This coordinator policy supersedes the earlier no-recovery statements for malformed
  output above; the synchronous gateway still issues exactly one request. All calls share the
  execution deadline, with two seconds reserved for persistence and a five-second
  connection cap. There is no HTTP/SDK retry or third dispatch after restart.
  After dispatch admission, the first call uses at most half the remaining model
  budget when a distinct second route is currently available and failover is enabled.
  Check persistent route health after dispatch admission; a cooling or occupied
  half-open alternate must not shorten the primary's original execution budget. Time
  is recalculated after database work; the second call uses the remaining budget
  under the unchanged deadline. A real-clock integration test verifies hanging
  primary cancellation, fallback success and one settlement with unknown usage retained.
- **Accounting:** business quota settles once for a validated saved draft, or is
  released on failure. Demo abuse limits count the user operation once; queued
  operations take the global demo execution slot only at dispatch. ProviderCall
  records route, fence, fixed error code, observed usage and bounded response ID,
  never credentials, prompt or arbitrary error bodies. An interrupted slot becomes
  unknown and cannot be reused; known usage remains on each call even if total
  usage is unknown. A pre-HTTP rejection is not_sent. Slots consume the conservative
  global daily dispatch budget even when their outcome is unknown or not_sent.
- **Health:** persistent route health opens after consecutive eligible failures,
  cools down, then admits only one half-open probe. Health generations reject stale
  observations. HTTP 426 immediately opens that route for at least 300 seconds,
  because an upstream upgrade needs operator action; after that, the same single
  half-open probe and generation fencing apply. Do not reset health merely because
  the incompatible upstream returned an HTTP response. Output validation failures
  are not network outages: malformed-output alternate eligibility must not set the
  stored transport `retryable` flag or count toward the outage circuit breaker. Dispatch-day
  counters survive tenant cleanup; they are not refunded by failure or deletion.
- **Rollback:** stop new admission with generation_enabled=false, leave background
  processing enabled until active work drains, then disable background/failover
  together. Keep migration history: downgrade refuses background operations, calls
  or used dispatch budgets. Different proxy hosts do not prove independent upstreams.

`tests/presales/test_presales_background_integration.py` covers real PostgreSQL/ASGI,
controlled HTTP failures, lease recovery, accounting and migration refusal.
`playwright.presales-background.config.ts` covers batch, refresh/navigation, partial
success, failed-only retry and tenant isolation. These checks do not change the v7
semantic release gate or replace single-attempt quality evaluation.

Only tenants with no entitlement history retain legacy behavior. Configured but
not currently active periods reject new generation with HTTP 403
`presales_entitlement_inactive`; insufficient quota is HTTP 429
`presales_usage_limit`, and usage-service failure is HTTP 503
`presales_usage_unavailable`. Preflight rejection rolls back the attempted row and
does not dispatch the model. Expiry does not block authorized reads, review, export
or replay of an already generated draft. Reservations retain their original period.

## Review, export and diagnostics

### Human completion without a model draft (0036 candidate)

1. **Scope / trigger:** pending/terminal failed rows must remain deliverable without
   another provider request. This is a local candidate; staging remains rc47/0035.
2. **Signatures:** `PUT /api/presales/{packet}/rows/{row}/manual-response` accepts
   `ManualResponseInput` with expectedRevision>=0, existing response/prerequisite fields,
   <=12 CitationInput values and a required note. `GET /{packet}/manual-evidence`
   accepts versionId, literal query<=200 and offset0..100000, returning <=10 snippets
   of <=600 characters and nextOffset. PresalesService owns both entry points.
3. **Contracts:** source browse/save reauthorize actor, tenant, packet ownership and
   frozen ready versions/generations. The save resolves literal quote containment and
   server filename/location. Existing status/citation/prerequisite validation applies.
   Tenant/packet/row locks serialize manual and generation writers. Never invoke
   retrieval/embedding/model, reserve quota, create a Job/attempt, cancel active work,
   or fabricate a successful attempt. Keep SavedDraft JSON unchanged as revision1;
   migration0036 adds nullable `presales_rows.manual_authorship` holding actor/time/note
   plus private idempotency key/fingerprint. Only actor/time/note leave the API.
   Review is a separate explicit action and retains the immutable original response.
   CSV leaves model-original columns empty for human drafts and appends human origin,
   author/time/note/original text and prerequisite columns. Workbook marks human origin.
4. **Validation / errors:** raw queued/running/recovering states (including not yet
   reconciled expiry) ->409 generation_busy; existing drafts ->409; stale revision or
   changed same-key intent ->409; foreign/nonliteral/stale evidence ->422/404/409.
   Replay reauthorizes and keeps later reviews. Model entitlement expiry does not deny
   authorized manual completion. Migration downgrade locks rows and refuses non-null
   authorship with `presales_manual_history_present`.
5. **Good/base/bad:** good: preserve a failed attempt, manually cite an authorized
   clause, review, return the workbook. Base: null authorship keeps existing model
   histories readable. Bad: count human text as model success or overwrite a worker.
6. **Tests:** `test_manual_response_integration.py` uses real owned PostgreSQL/API,
   no-embedding boundary, expired entitlement, replay, concurrent saves, active states,
   literal/source/tenant checks, failure ledger, audit redaction, separate review,
   XLSX/CSV and downgrade refusal. The workbook browser suite covers1440/390px and
   a lost committed PUT response followed by GET, without a second write.
7. **Wrong vs correct:** old rc47/rc46 strict draft decoders can parse unchanged JSON
   but cannot interpret authorship: they are NOT valid rollback readers once human
   history exists. Do not erase attribution or downgrade0036. Prepare coordinated
   API/Worker/Web release and verified reader guards before staging writes; current
  0035 release tooling rejects0036. Human completion is separate from model quality.

### Complete human prerequisite correction (2026-10-08 candidate)

The review PUT now optionally accepts `prerequisiteChanges: {origins: (number|null)[],
excludedIndexes: number[]}`. Each effective prerequisite has exactly one origin:
an immutable zero-based original draft item, or null for a human addition. Repeated
origins allow splitting one mixed proposition. Exclusions are unique, disjoint from
referenced originals, and together must account for every original item. Reference
indexes remain strict integers in 0..11; no fabricated origin or silent omission.
This permits rewriting, splitting, adding and explicitly excluding human conclusions.
It does not alter the model draft or provide new source evidence.

Use `validate_review(draft, payload, previous)` for the shared contract. Existing
schema length/status/conditions rules and ModelDraft citation validation still apply;
citations can only reference the saved, authorized draft evidence. Changed content or
mapping relative to original/latest review requires a nonempty note. Without a change
map, old clients retain the exact original structure restriction. Null legacy fields
remain unrecorded; an explicit new map can introduce human assessments with null
origins. It must not pretend those were model-generated. Null change maps are excluded
from old request fingerprints, including the pre-prerequisites legacy path.
Always load the latest review when a row already has review history, including a
request with null prerequisites. Reverting explicit human additions on a legacy
unrecorded draft is a change and requires a note; rejection must not write a revision.

Migration `20261008_0033` adds nullable JSONB `presales_reviews.prerequisite_changes`
with SQL NULL for old reviews and an object constraint for new metadata. `content`
retains the pre-0033 SavedReview fields, so the old strict reader can still decode
corrected review bodies on the newer schema. The existing history aggregation merges
the side column in the same MVCC statement; no per-row reads are added. A schema
downgrade locks the review table and refuses any non-null correction history with
`presales_review_changes_history_present`. Preserve 0033 for application rollback;
do not delete provenance. Actual signed-image rollback still needs E verification.

Outputs/history include their own mapping. CSV appends `人工前提修订记录` with effective
origins, human additions and excluded original text; original/effective evidence columns
and formula escaping remain. RowView and the Web parser validate each history mapping
against the immutable original and all evidence indexes before display/export.

| Case | Result |
|---|---|
| Split/add/rewrite/exclude, valid map and note | New immutable revision; same-key replay is unchanged |
| Missing origin, duplicate exclusion, malformed fields | API 422 request_validation_failed |
| Unaccounted, out-of-range or both excluded/referenced original | 422 presales_review_prerequisites_invalid |
| Change without note | 422 presales_review_note_required |
| Out-of-range saved evidence index | Existing 409 presales_review_evidence_required |
| Stale revision / changed same-key intent | Existing 409 conflict; no new history |
| Revoked member/source | Existing authorization rejection, including replay/export |

Tests: `test_presales_review_changes_integration.py` calls the real PostgreSQL/API,
preserves original JSONB and two revision snapshots, exercises conflicts/revocation,
all-original exclusion and legacy additions, and verifies real migration behavior.
`legacy_review_schema.py` is a frozen extraction of the pre-change strict decoder,
independent of Git clone depth; do not evolve it with production schemas. Both browser
widths test split/add/exclude, reload, provenance, original/history and downloaded CSV.
Controlled fixture success is manual-correction capability, not original model accuracy
or independent business approval. R5 model quality remains separately open.

Source snapshots fetch authorized versions, active generations and latest versions
in one statement, preserving input order/applicability. Missing or ambiguous sources
fail closed. Packet reads fetch rows and separately aggregated attempts/reviews in
one PostgreSQL statement, preserving each row's ordered history without a join
cross-product. Draft/revision and history must use the same statement snapshot:
separate SELECTs can mix an old missing draft with a newly succeeded attempt and
briefly report a successful generation as failed. A concurrent review must likewise
not appear alongside an older row revision. Keep initial and final authorization/
source rechecks outside that snapshot so revocations during assembly are observed.
The complete 6-source/12-row read has an eight-SELECT integration budget. Regression
tests finish generation or review immediately after the row SELECT and verify the
current response stays coherent, while the next request sees the committed result.

### Original structured prerequisite review (historical CO-1/CO-2 contract)

1. **Scope / trigger:** the adapter previously discarded prerequisite states and
   links, presenting both unmet and unknown items as unmet conditions. Retain this
   information without inferring or correcting model semantics. The frozen v7
   unknown-to-unmet failure and release gate remain unresolved.
2. **Signatures:** `ModelDraft`, `SavedDraft`, `ReviewInput` and `SavedReview` include
   `prerequisites: list[PrerequisiteAssessment] | None`. GET/create/generate/review
   keep their existing routes; JSONB draft/review content persists the extension
   without a migration or rewriting existing rows. `resolve_selection(content,
   catalog)` binds request-local references to the ordered union of saved citations.
3. **Contracts:** each item is `{condition, state: met|unmet|unknown,
   citationIndexes: number[]}`. Up to 12 items; each has 1–12 unique, strict integer
   indexes into original `draft.citations`, starting at 0 and within bounds.
   `null`/omission means not recorded by an older contract; `[]` explicitly means
   no prerequisites. `conditions` must equal the ordered, exact-text deduplication
   of non-met prerequisites. Reviews retain every original condition and reference
   in order, editing only state. A nonempty note is required when states differ
   from either the original draft or the latest review. Original drafts and prior
   revisions stay immutable. No new environment setting is required.
4. **Validation / errors:** inconsistent status/conditions, malformed/duplicate
   indexes or oversized fields fail schema validation (API 422; model output fails
   without repair). Missing, added, reordered or rebound review prerequisites:
   422 `presales_review_prerequisites_invalid`. Changed states without a note:
   422 `presales_review_note_required`. Existing evidence, revision, idempotency
   and tenant/source authorization checks still apply. Legacy null reviews omit
   this field from their fingerprint to preserve pre-upgrade idempotent replay.
5. **Good / base / bad cases:** purchased=met, unfinished configuration=unmet,
   missing acceptance record=unknown remain three separate items and references.
   A reviewer may correct a state with an explanatory note; this is a human
   assessment, not newly acquired source evidence. Legacy conditions remain editable
   and show `未记录前提状态`. Do not accept a reviewer dropping an unknown prerequisite
   to obtain supported, or pretending an older missing list is an assessed empty list.
6. **Tests:** HTTP selection tests assert all states and reference union; real
   PostgreSQL/API tests assert persisted links, correction history, legacy replay,
   untouched original JSONB, rejection of dropped/rebound items, revocation and CSV.
   Web tests exercise per-item state edits/notes; 1440px and 390px browser journeys
   cover matching evidence, reload and downloaded CSV. These tests use synthetic
   sources and controlled model HTTP; they do not establish real model quality.
7. **Wrong vs correct:** wrong: derive state from words in `conditions`, or amend
   old failed evaluation results. Correct: retain explicit assessments, display
   unknown distinctly and keep historical scores. New gateway runs use
   `presales-gateway-run-v3`, whose recorded result must include and exactly match
   the structured raw response. v2 scoring reproduces only its original flat
   projection; v1 scoring and failure denominators remain unchanged.

CSV changes the ambiguous `未满足条件` header to `响应条件`, and appends
`前提状态与对应证据` plus `原模型前提状态与对应证据`. Each item carries its state,
condition, filenames, source versions, locations and exact excerpts. Legacy missing
assessments are explicitly unrecorded; ungenerated rows remain blank.

Release API/Worker and Web together. The new strict Web parser accepts omitted
legacy fields as null. Older strict server/Web builds cannot read rows containing
the new fields: a rollback image must understand this contract. Do not remove the
new persisted state/history to make an old build start. This change is not deployed.

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

## Scenario: Original Excel questionnaire delivery

### 1. Scope / Trigger
Customer XLSX intake and original-format delivery reuse Presales packets, evidence,
generation and reviews. They do not ingest questionnaire cells as retrieval sources.

### 2. Signatures
`POST /api/presales/workbooks/preview`, `POST /api/presales/workbooks` with
`Idempotency-Key`, and `GET /api/presales/{id}/workbook?mode=draft|reviewed`.
Migration `20261009_0035` adds nullable JSONB `workbook_metadata` and deferred bytea
`workbook_content` to packets; normal packet reads must not load the bytes.

### 3. Contracts
Bound streamed JSON before Pydantic parsing. Payload carries filename/contentBase64;
mapping has sheet/questionColumn/answerColumn/firstRow/lastRow. Import also carries
title/sources/confirmedSha256. Reparse original bytes, bind fingerprint to mapping
and SHA256, and atomically save file/rows/metadata under the existing tenant lock.
Manual create stays <=12 rows; imported packets <=120; generation admissions stay <=12.
`PacketView.workbook` exposes metadata only. Existing demo and generation limits remain.
Originals have an independent 20 MiB tenant storage cap; they are retained/deleted
with packets and are not counted as document-ingestion or embedding consumption.

### 4. Validation & Error Matrix
2 MiB compressed, 20 MiB expanded, 256 members, 20 sheets, 50,000 physical cells,
row<=10000/column<=256; limit -> `presales_workbook_size`/413. Invalid ZIP/XML,
duplicate/misaligned cells -> `presales_workbook_invalid`/422. Unsupported macros,
signatures, external links, active embedded parts or protection reject. Question
cells must be visible unmerged text <=2000 characters. Populated/formula/merged/
validation/table/formula-range targets -> `presales_workbook_target`/422. Check grouped
hidden column bounds and array/shared formula ranges, not only the target cell value.
Storage cap -> 429; mismatched confirmation -> 409; reviewed export before all reviews ->409.

### 5. Good/Base/Bad Cases
Use openpyxl to read; write inline strings through bounded lxml/ZIP editing. Copy all
unmodified ZIP member bytes and preserve unrelated worksheet nodes. Never save through
a lossy whole-workbook serializer. Mark unreviewed/missing/failed rows, retain conditions
and missing information, reject overflow rather than truncate. Existing formula caches
are not recalculated. Reauthorize source/actor access before returning download bytes.

### 6. Tests Required
Real XLSX round-trip, unchanged ZIP members/styles/formulas, formula-like text, unsafe
targets and malformed bounds; real owned PostgreSQL replay/conflict/isolation/quota,
deferred file reads, review/export/revocation, migration round-trip and history guard;
desktop/mobile browser download and reopen. No public-schema migration or live provider.
Examples: `tests/presales/test_workbook.py`, `test_workbook_integration.py`, API body tests,
`apps/web/presales-e2e/workbook.spec.ts`. Browser fixture cleanup must run over its test-only
endpoint before Windows process termination; write the owned schema and cleanup receipt.

### 7. Wrong vs Correct
Wrong: raise batch/provider limits to 120, store original bytes in browser storage,
or downgrade away imported history. Correct: retain <=12 admissions, persist file and
mapping server-side, expand schema before coordinated API/Worker/Web release, and refuse
downgrade with workbook history. Existing rc.45 release scripts stop at 0034; a separate
0035 release/rollback plan is required before deployment. Local checks do not certify
commercial quality or native desktop Excel support for arbitrary features.
