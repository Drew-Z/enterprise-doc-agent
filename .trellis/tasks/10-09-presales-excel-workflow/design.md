# Excel questionnaire workflow

User authorization: implement the previously proposed bounded Excel workflow (2026-10-09). Reuse canonical checkout and the commercial task recovery group; no deployment or new provider acceptance claim.

Flow: authenticated bounded JSON upload -> XLSX inspection -> user confirms one visible worksheet, question/answer columns and row range -> server reparses identical SHA256 content -> atomic packet, requirements, mapping and original-byte persistence -> existing per-row generation/review -> authorized original-workbook export.

Use openpyxl for reading Excel values/coordinates and lxml for a narrow OOXML worksheet edit. Do not save through a workbook reserializer: copy all ZIP members except the selected worksheet verbatim, and retain all unrelated nodes/styles there. This is application runtime code, not an authored spreadsheet artifact. Answer values are inline strings, never formulas. No formula evaluation, macro execution, external URL fetch or provider calls during import/export.

Boundaries: ordinary transitional XLSX, 2 MiB compressed, 20 MiB expanded, 256 ZIP members, 20 worksheets, 50,000 physical cells, rows <=10,000 and columns <=256. At most 120 nonblank questions in an explicitly selected range. Reject encrypted/macro/signed/embedded/external-link packages and unsafe XML/ZIP; reject protected/hidden selected sheets, hidden selected questions, merged/formula questions or answer targets, populated targets, table/validation targets and oversized text. Return stable safe errors, no file bodies in logs. Preview is ephemeral and requires membership authorization. Creation checks existing demo limits and a separate 20 MiB original-workbook tenant storage cap under the existing tenant lock. This attachment storage is bounded PostgreSQL bytea, retained/deleted with its packet/tenant, not a document ingestion or embedding.

Add nullable workbook metadata and deferred bytea on PresalesPacket in additive migration 0035. Manual packets remain <=12 rows. Imported packets use an internal validated create payload <=120; batch admission remains <=12. Fingerprint includes original SHA256 and confirmed mapping; same-key replay rechecks authorization and returns the same packet. Packet metadata contains filename, SHA256, selected mapping, ordered Excel rows. Never include bytes in packet GET. Downgrade refuses if any workbook exists; old release readers may not support imported packets, so deploy API/frontend together and retain schema on application rollback.

Frontend shares title/source selection with manual entry. File lives only in component memory, discarded with form/auth-context unmount. Mapping changes invalidate confirmation. Display exact question/answer cells before creation. Reopen uses durable metadata. Paginate rows in groups of 12; generation buttons submit only the next <=12 pending/failed rows, without automatic replay or quota increases. XLSX export and existing audit CSV remain distinct controls. Draft workbook marks unreviewed/unavailable rows; reviewed export requires all rows reviewed. Both include conditions and missing information without silent truncation; detailed evidence/review history remains in existing CSV and app.

Mock only provider/network boundaries. Test genuine ZIP/XLSX parsing, persistence and real PostgreSQL constraints in owned loopback schemas; browser assertions cover upload, mapping confirmation, reload, batch boundaries and download. No public schema migration.

## Approved release continuation

Reuse the existing supervised expansion executor with a second fixed migration, 0034-to-0035. Plans select only a known original/target pair and the exact built-in SQL digest. The same credential-private psql session, advisory lock, deadlines and resource fences apply; compare complete column/check shapes and recover by observing the committed state, never by replaying or dropping columns.

On 0035, an image-only plan explicitly binds whether original and candidate images support workbook records. A legacy side requires an empty-workbook read before writes and again after admission closes. Check both deployment and recovery sides before apply, and the original side before restore. Preserve all metadata/content and the expanded schema. rc.45 may be rolled back to only before workbook import; after history exists, use a compatible candidate or a forward fix, never erase history to make rollback possible.

Freeze the public source, original file hash, selected range, source applicability, call budget and review criteria before product replay. Keep this file-flow exercise distinct from the previously completed six-call generation comparison and failed commercial acceptance.

## Focused public replay remediation

Read-only observation confirms PostgreSQL simple full text treats the unspaced SWU03 query as one whole token; neither primary nor OR fallback matches any of17 chunks, including two containing the required clause. Add a bounded character n-gram fallback within the existing authorized document query when primary full-text recall is empty. Keep one keyword query roundtrip and existing vector calls/RRF/top-k; do not infer semantic truth from lexical matching.

Output failure details were not persisted, so the historical invalid outputs cannot be reconstructed. Add allowlisted diagnostic categories to PresalesError and existing attempt provenance (per dispatch number for background, one field for synchronous), never raw content. Keep public error codes, outage/recovery classification, usage and deadlines unchanged. Do not claim this diagnostic patch alone fixes historical model failures.

Prompt v15 keeps the v14 literal-basis schema/decoder and adds scope/negative-evidence guidance plus relevant-citation selection. It cannot deterministically prove entailment. Old decoder reports and saved attempts remain unchanged; new admission freezes the new prompt SHA. A deployment must drain old accepted policies before switching.

## Human response persistence and compatibility

Keep SavedDraft JSON unchanged as the immutable response baseline; add nullable row manual_authorship JSONB in additive migration0036 with server actor/time/note and private operation-key/fingerprint. Public RowView exposes only actor/time/note. Null retains historical model attribution. The same row.draft fence protects against generation overwrite and existing review/export rules are reused. Manual creation writes revision1, no attempt/review success is fabricated; review remains a separate explicit action. Existing drafts cannot be replaced. All raw active attempt states reject, including expired-but-unreconciled executions; no implicit cancellation or settlement.

Manual evidence browse is a reauthorized, bounded literal substring search within packet snapshot versions/generations,10 chunks per page with600-character snippets. No retriever/provider is called. Save resolves every chosen chunk and exact substring against the current authorized generation and reconstructs filename/location server-side, then applies existing response/citation/prerequisite rules. Tenant/packet/row lock ordering serializes competing manual/generation writes; same-key replay precedes revision checks and always reauthorizes.

Migration downgrade locks rows and refuses any manual history. Older rc47/rc46 JSON readers can parse the unchanged draft bytes but misattribute human content; they are therefore NOT compatible rollback targets once manual history exists. Do not deploy0036 or write staging manual records until coordinated API/Worker/Web release and explicit manual-reader rollback guards are implemented and verified. Existing0035 release tooling fails closed on0036. The initial phase validated local product behavior; the authorized release continuation below subsequently satisfied those deployment guards. Preserve frozen public packets.

## Authorized manual release continuation (2026-10-10)

Extend the fixed executor with exactly0035-to-0036, retaining all original images/configuration. Validate the complete0034/0035 column/check shape plus the nullable, default-free manual_authorship JSONB and its exact validated check. Share the existing advisory lock, private psql session, timeout/readback and atomic transaction. The expansion plan binds unchanged workbook-reader capabilities; recovery observes either complete revision without replay or downgrade. Expansion recovery must refuse original applications if manual history has appeared, both before closing and before reopening.

On0036, image-only plans require separate exact boolean original/candidate workbook_readers and manual_readers. Apply checks both deployment and recovery readers; restore checks the original reader. For each incompatible capability, require empty corresponding history before writes and after all applications stop. A racing submission keeps applications stopped and cannot change image/configuration/credential bindings. Existing workbook history must remain readable throughout. No configuration, provider route, retry or budget changes.

Validate through ReleasePlan/ReleaseCluster and PsqlSession boundaries: controlled Kubernetes I/O and clock, actual Alembic SQL and owned PostgreSQL schemas. Cover migration rollback/lost receipts/shape drift, both history races, compatible recovery with real human responses, and immutable workbook/human metadata. Then publish exact signed images, expand, release, exercise rc47 rollback only before manual history, and reapply. New owned manual-only live fixtures verify evidence -> save -> review -> export without provider calls. Preserve the frozen public packet and all failed model results. After human history exists, recovery requires compatible images or forward repair.

Executed: rc48/0036 passed expansion, release/rc47 rollback/reapply, followed by one new
human-only workbook and the actual history-aware refusal through a read-only Kubernetes
boundary. Human records now exist, so the pre-history rc47 drill is not an available
current rollback. Input workbook creation belongs outside the read-only application
container; the product imports existing bytes and exports through its narrow OOXML path.
The corrected acceptance harness follows that boundary without relaxing runtime mounts.

## Review evidence correction

ReviewInput adds nullable citations (<=12 unique CitationInput values). Omission/null
retains the historical original-draft contract and fingerprint. Once the latest review
has its own citations, a new review must explicitly supply its evidence; an old request
cannot silently revert it. Existing-key replay remains valid and reauthorized.

Resolve explicit citations against the packet's tenant/version/active-generation and
literal text, using the same bounded resolver as manual entry. Derive locations on the
server. Validate status, conflict and prerequisite indexes against this exact ordered
set. Changing evidence or prior assumptions requires a note. Preserve the tenant/row
transaction and final authorization checks; no generation/accounting operation occurs.

Add nullable JSONB presales_reviews.citations in0037. SavedReview exposes the full
server-resolved evidence snapshot; keep historical content JSON unchanged and merge
the side column in the existing MVCC history query. Each revision's indexes refer to
its own evidence; null means immutable draft evidence. Downgrade locks and refuses any
non-null citation history. rc48 cannot interpret new citations, so no staging writes
until a coordinated release and reader guard is verified; existing0036 tooling rejects0037.

Extract the existing literal evidence selector for reuse by manual creation and review.
On selection changes remap retained prerequisite references by exact evidence identity;
removed links become visibly unselected and block save until the reviewer resolves them.
Show effective, original and historical evidence separately, and append original-evidence
and correction provenance to CSV. Do not reset the response form when browsing evidence.

## Review citation release continuation

Reuse the supervised fixed expansion executor with exactly0036-to-0037 and the same
private psql session/advisory lock/deadline. Retain all original images and configuration,
and bind unchanged workbook/manual reader capabilities. Verify inherited schema plus
nullable/default-free citations JSONB and its exact validated array/length constraint.
Recovery reconciles complete0036/0037 without replay or downgrade; any nonnull citation
history on0037 prevents reopening original0036 readers before writes and before reopening.

Image-only0037 plans require independent boolean workbook/manual/citation reader
capabilities for original and candidate. Apply checks both sides, restore original;
each incompatible history is checked before writes and after applications stop.
Preserve existing human/workbook history, credentials, provider settings and all failed
sample results. Validate controlled cluster races and actual owned PostgreSQL migration,
unknown commit receipts, schema drift, real corrected review/export and refusal with[].
Publish and validate exact source before signed release. A live rc48 rollback drill may
occur only before new citation history; afterwards use compatible images/forward repair.

Executed: rc49 / 0037 passed the schema window and release/rc48 rollback/reapply before
independent citation history. A new human-only fixture then verified two separate review
snapshots, original draft/authorship, corrected export and unchanged accounting. Actual
restoration now refuses rc48 before writes. Capacity stayed under the existing complete-batch
guard after the specifically approved rc36–37 cleanup; no reserve or import-contract change.

Live fixture identity binds persisted imported key X7, exact question and B7/C7 location;
SWU03 is a public replay label, not the application's imported row key. The first harness
failed before packet creation, was diagnosed read-only and retained. No product or model
behavior changed during that correction. Keep historical samples immutable.

## Generation diagnostic evidence

The collector currently catches PresalesError but saves only errorCode, discarding the
allowlisted diagnostic_code already produced by the real gateway. Add optional
errorDiagnostic only when present; retain the existing v6 decoder, traces and failed
state. Do not store exception strings or infer a category when the gateway has none.
Controlled HTTP proves retention, safe fields and unchanged no-retry behavior before
any fresh public-source diagnostic. Keep live input and separately frozen review criteria
outside the collector; existing source/hash validation and exclusive output creation apply.

## Coverage candidate

Keep the v14/v15 BasisDraft field names, bounds, validators and projection unchanged.
Describe proposition as a concrete business event/capability to assess; definition alone
contains the rule establishing necessity. Explicitly distinguish known rules from unknown
current facts. Describe answer as the response to every requested aspect, and require
specific missing arrangements rather than an invented plan or a generic proof request.
Use prompt v16 so new execution policies bind the changed system/schema description SHA.

This is a model-instruction change, not deterministic semantic enforcement. The original
CQU03 outcome is the observed failing baseline; controlled schema tests cannot prove the
model now understands it. Preserve historical decoders and score bytes. Before one fresh
CQU01 request, freeze the candidate and separate coverage/event criteria; retain failures
and reject broad quality claims. Staging remains rc49/v15 until an exact compatible release.

## Separate answer-aspect protocol

Add a private CoverageDraft with ordered answers (1..12), each holding requirementText,
answer, citations and missingInformation, plus independent BasisPrerequisite[] and
overall status. Require each requirementText to match the next exact substring of the
original question, allowing only whitespace between spans; the complete remainder must
be whitespace. This proves text coverage/order only, not a meaningful semantic split.

Render each exact question span with its own answer in the existing answer string;
union selected citations and missing-information entries in first-use order. Pass this
projection through the unchanged literal-basis resolver and public limits. Preserve
quote, reference, Chinese-prose, status and prerequisite validation; overflow rejects
without truncation. No stored schema change or semantic relabeling is involved.

First validate the module boundary with actual parsers and exact source fixtures, then
probe the distinct private protocol with one frozen CQU04 request before changing the
runtime gateway. A successful candidate requires a new prompt identity and run-v7 scorer
branch; old v1..v6 parsers and outcomes remain frozen. The gateway must bind coverage to
each call's own question, with no shared mutable state. Accepted old prompt policies
must drain before release. Do not widen routes, retry bounds, source authorization or
accounting to make a candidate pass.

Observed outcome: v17 failed its first CQU04 schema and semantic checks. The
private module and tests are archived with exact hashes outside runtime source.
Do not implement the conditional gateway/scorer promotion for this rejected
candidate. Complete question-text coverage did not force substantive answers.

## Strict provider request mode

Use StrictBasisDraft inheriting BasisDraft with the two optional arrays made
required, so the Pydantic schema has no optional object properties. Keep the
original decoder and legacy prompt exactly unchanged when disabled. In strict
mode, send response_format json_schema with strict=true and this same schema;
validate StrictBasisDraft locally before the normal literal-basis projection.

Independent primary/fallback settings default false. Strict mode uses presales.v18
and includes its schema in the system-message hash; existing RoutePolicy version
and hash bind the request contract without adding storage fields. Restoration
must preserve the selected mode and reject a differently configured template.
Changing mode requires draining accepted old prompt policies before rollout.

Strict evaluation reports use run-v8; v7 remains the rejected answer-aspect
candidate and is not accepted by historical scorers. Add explicit v8 resolver
branches; legacy v1..v6 interpretation remains unchanged. No output coercion,
retry, model/channel switch, database migration or product quota change.

## Provider-visible evidence combination branches

New strict v19 uses four Pydantic prerequisite variants with unchanged fields.
For uncertainty none, exactly one of positive/negative has minItems1 and the other
maxItems0; unconfirmed is empty. Missing requires both direction arrays empty and
allows unconfirmed records. Conflict requires both direction arrays nonempty and
unconfirmed empty. A nondiscriminated Union emits anyOf without unsupported custom
schema rewrites; structural branches are mutually exclusive by literals and counts.

The root duplicates only the five stable wire fields and then reuses resolve_basis
for all shared status/quote/language/public projection checks. Preserve the v18
StrictBasisDraft and strict_response_format definitions for run-v8 scoring.
New strict collection emits run-v9, with explicit format/resolver branches in both
scorers. Legacy JSON-mode identity is unchanged. New strict identity uses the same
frozen policy guard; historical v18 policies cannot silently become v19.

## Discriminating v19 observation

Reuse the production gateway and run-v9 collector with exactly one new synthetic
requirement. Include four independently worded prerequisite rules plus separate
same-scope records: positive, negative, explicitly missing and contradictory peer
records with no stated precedence. Freeze a gold file separately; never pass it
to the collector. Assess array/schema validity separately from event decomposition,
direction, genuine conflict and answer/missing-information coverage.

Bind the current source hashes, controller hash, v19 provenance, primary endpoint
digest and deployed policy; recheck active jobs and daily usage immediately before
dispatch. Reserve one direct call durably before entering the collector. Refuse
existing intent/output paths and expired plans. No retry after a timeout, unknown
receipt or rejected output. Preserve the source report and a read-only postflight;
only observations and documentation may change in this continuation.

Observed boundary: source identity and legal array combinations do not constrain
the text of a quote or its semantic direction. The v19 output reconstructed two
quotes by attaching shared date/order context to a later clause; strict literal
validation correctly rejected them. Missing rehearsal records were also assigned
to negative evidence while the prose called them unknown. Do not normalize quotes
or infer correctness from prose. Investigate immutable offered evidence selections
as a separate protocol design; this alone would not establish semantic entailment.

## Strict span-selection protocol

Reuse prepare_citations and its call-local source catalog. For each authorized
excerpt, offer its complete text and exact punctuation-delimited substrings as
immutable span IDs. Keep the full existing evidence text/source metadata alongside
the spans, including shared subjects and qualifications; boundaries are lexical,
not asserted atomic business events. Discard only surrounding whitespace and
deduplicate exact repeated spans within an excerpt. Existing request-byte limits
reject oversized expanded inputs before dispatch, with no truncation.

SpanBasisDraft retains the four support alternatives and stable top-level fields,
but every quote becomes only {spanId}. Deterministically resolve each ID through
the original catalog into its parent citationId and exact substring, then reuse
ConstrainedBasisDraft and resolve_basis. This is a declared selection protocol,
not a repair of generated quotes; old free-text objects are invalid. No model
semantic choices are changed by the server. Keep source authorization and public
projection unchanged. Span catalogs are local values, never gateway instance state.

New strict identity is v20; legacy JSON remains v15. New run-v10 traces record the
offered spans. Scorers verify the entire reconstructed input/spans against the
frozen source input before interpreting any result; v8/v9 keep their old formats
and resolvers. Accepted v19 policies cannot silently resume as v20. Source code
and decoder checks precede any later bounded real-provider observation.

## V20 discriminating observation

Reuse the run-v10 collector, real gateway, existing credential loader and read-only
staging preflight; the reference never enters the request. Bind the complete input,
criteria, candidate sources, controller/helpers and route identity before one
dispatch. Recheck the UTC day, active work, deployed policy and conservative direct
reservations immediately before recording exclusive intent. Carry the prior18
known/unknown reservations even across a UTC rollover rather than erasing them.
Record the first raw report and separate semantic review. Five independent events,
adjacent contrary/missing statements, absent training documentation and other-order
facts test the remaining meaning boundary. Do not alter product source or repair
generated output during this observation; keep all previous failures immutable.

Observed: fixed IDs eliminate transcription in this request but leave role and
entailment errors intact. Definition and state selections are separate fields,
yet four definitions used state records. The decoder accepted a genuine missing
record assigned to negative. Do not infer semantic quality from the accepted
projection or aggregate source coverage; any subsequent design must explicitly
address evidence roles and per-item requested actions without keyword relabeling.

## Standalone assessment candidate

Use a distinct private protocol identity and strict schema, reusing the v20 offered
span catalog. `rules` contains affirmative business propositions and selected
`requiredBy` spans; `assessments` binds every zero-based ruleIndex exactly once.
Four mutually exclusive variants carry met evidence/no action, unmet evidence/a
completion action, missing observations/a confirmation question, or conflicting
peer evidence/a reconciliation question. Require a short source-grounded state
summary, not hidden reasoning. The model remains responsible for each semantic
choice; do not reject legal combined rule/state text or relabel unknown words.

Ordered `responses` contain exact requirementText substrings, an answer, selected
citations and specific information gaps. Reject omitted, duplicated, reordered or
invented requirement text; whitespace gaps are allowed. This verifies textual
coverage, not independent semantic aspects. Require source evidence or an explicit
gap per response. A conclusion, all per-part answers and every state/action block
are rendered into the unchanged public answer string. Preserve unknown/conflict
questions individually in missingInformation, and reuse resolve_span_basis for
literal source, language, final limits and business validators. Validate each raw
prose field before adding Chinese formatting so labels cannot hide English prose.

Do not add a runtime setting, replace gateway v20, reinterpret run-v10 or change
storage/UI/export in this candidate phase. The candidate schema/identity and local
resolver are independently testable before an actual observation and conditional
integration. A source span may legitimately be both a rule and a fact; field-level
role separation is not a semantic oracle and must be tested/documented as such.

## Isolated candidate observation adapter

Use a dedicated one-request process and a scoped adapter over the existing gateway
schema/prompt/resolver bindings. Keep its transport, streaming, request/response
limits, source offering and accounting unchanged. The candidate resolver closes
over that request's original requirement; restoration leaves v20 identity intact.
Controlled HTTP success/503 checks precede any real dispatch. Record a distinct
presales-assessment-candidate-run-v1 report; never relabel it as historical run-v10.
Freeze all adapter/helper/source hashes and separate criteria, then recheck live
identity, active work and budget immediately before exclusive intent. Raw outcome
and per-criterion semantic review stay separate; do not coerce a failed response.

The AR01 result rejects this design's promotion: source IDs and schema are valid,
but responses contain rule text instead of exact requirement text. Structured
states also disagree with generated prose, and source applicability is violated.
The adapter remains an isolated diagnostic; production gateway/settings/scorers
continue unchanged. A separately planned minimal contract on the same primary
route should investigate whether protocol burden or basic interpretation causes
these errors, with the same semantic standards. This is an investigation direction,
not a new runtime design, authorization to rerun AR01, or proof of either cause.

## Minimal generation diagnostic

Reuse complete prepare_citations input, RecordingTransport and the bounded
OpenAIResponseReader, but use a separate local diagnostic collector with JSON mode
and a single answer string. Keep the primary endpoint/model/reasoning effort,
streaming, 120-second deadline, 4,000-token request cap and 4,000-character answer
limit unchanged. Do not feed the answer into a public draft, historical scorer,
application persistence or a relaxed production validator. Preserve source names
and applicability, all original text and the first raw provider response.

Freeze exact request bytes, source/helper/controller hashes and independent gold;
verify current policy, ledger and conservative direct reservations before exclusive
intent. Test streaming success, HTTP 503, invalid shape and incomplete output at
the HTTP boundary, one mock dispatch each. Output format is
presales-minimal-contract-run-v1. A new-source observation is not a matched ablation;
report only the evidence it supplies about interpretation under simpler formatting.

Observed MC01 does not support a formatting-only remedy: a 959-byte system prompt
and one answer field still produce unsupported actions and incomplete coverage.
The collector and diagnostic decoder remain central evidence, outside product code.
No source repair, public-draft coercion or automatic semantic classifier is added.
Next investigate the same primary route's inference settings with separately frozen
criteria before changing the product design; this result does not prove a model's
universal capability limit or justify a production configuration change.

## Paired reasoning diagnostic

Reuse the frozen minimal-observation collector unchanged. Prepare source/citation
input once, then clone its request with only reasoning_effort set to medium or low.
Run medium first, then low, in independent HTTP calls with no prior response in the
second input. Persist both planned arms before dispatch and each arm's first/final
state. Reserve two direct calls before starting; recheck unchanged primary policy,
source/ledger identity and active work before each arm. Stop rather than resume an
unknown request. The same 120-second deadline and 4,000-token/character limits apply.

Freeze input, separate reference, both exact request hashes, helper/source hashes,
arm order and decision rules. If medium alone passes, investigate its compatibility
with the actual product contract before promotion. If both fail, do not treat the
reasoning setting as a demonstrated remedy. Both passing or low-only passing also
requires further evidence, not an automatic configuration change. Transport or
incomplete observations cannot establish a semantic contrast. Record requested
effort separately from provider-returned metadata; its internal enforcement is
not independently attested. Use presales-reasoning-comparison-run-v1 only.

Observed: medium and low both pass the minimal format but fail meaning criteria.
The only request difference is the requested effort string; both retain the same
full evidence and prompt. Medium changes the unsupported test assumption from
negative to positive, not to known uncertainty, and retains the extra transcript
duty. Keep both outputs and reject a production setting change. A prospective
same-endpoint glm-5.3 qualification is the next inference-quality investigation;
do not infer its availability from GET /models or silently substitute it in runtime.

## GLM qualification adapter

Reuse the unchanged isolated assessment adapter and real gateway, including its
strict schema, prompt, source-span offering and public resolver. Copy settings from
the currently verified primary policy and change only model_name to glm-5.3 inside
the dedicated diagnostic process. Bind the same endpoint and credential source;
never load fallback credentials or alter local/staging configuration.

An HTTP-boundary transport asserts the actual model, endpoint, effort, prompt,
schema, limits and complete source projection before forwarding, then records only
the credential-free request body and its hash. Controlled success/503 prove the
override, one dispatch and restoration of the original gateway identity. Freeze
all source/helper/controller hashes and a separate reference before exclusive intent.
Use presales-primary-model-qualification-run-v1, retain raw results separately from
review, and reject promotion on identity, mechanical or semantic failure.

GP01's original resolver error is `responses must cover the exact requirement in
order`; strict schema and complete source/span binding pass. All three response
items copy the entire question. No result is normalized or passed through a relaxed
resolver. Requested glm-5.3 returns z-ai/glm-5.3; GET /models advertises only the former
with owned_by=custom and proves no canonical alias mapping. The verification action
is ambiguous despite correct unknown/missing classification. Keep both findings.
