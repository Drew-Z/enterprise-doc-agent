# Customer Excel questionnaire workflow

## Goal

Import a customer XLSX, confirm its question/answer columns and row range, generate and review using existing evidence, and download the answers in the original workbook. User explicitly authorized implementation on 2026-10-09.

## Requirements

- One selected worksheet; up to 120 nonblank questions; preview and explicit mapping confirmation.
- Preserve original formatting and unrelated content. Reject populated/formula/merged/protected answer targets and unsupported input visibly.
- Reuse source applicability, generation, evidence, human review history and individual failure recovery.
- Original file and mapping survive refresh/reopen. Draft export marks unreviewed and unavailable rows; reviewed export requires every question reviewed. Include effective conditions and missing materials. Keep audit CSV available.
- Import/export makes no model or embedding calls. Existing daily, batch, demo and concurrency limits remain enforced.
- Tenant/author authorization, source revocation, idempotency, bounded input/storage and redacted errors apply throughout. No file bodies in browser persistence.
- Exclude macros/encryption, arbitrary Excel features, multi-sheet aggregation, answer reuse/assignment, model routing changes and deployment.

## Acceptance Criteria

- [x] Real XLSX preview gives exact question and answer cells and skips blank question rows; malformed or occupied targets reject. No parsing mocks.
- [x] Real workbook export reopens with literal response text, conditions and missing information; unrelated XML members/content remain unchanged.
- [x] Atomic import replay/conflict, tenant isolation, limits, reload and export pass in owned PostgreSQL schemas; only provider calls are controlled.
- [x] Desktop/mobile UI exercises upload, mapping confirmation, bounded generation, review, reload and download.
- [x] Required project checks completed; prior commercial acceptance failures remain recorded and this change is not presented as real-user time savings.

## Notes

- Local acceptance completed on 2026-10-09; see `validation.md` and `docs/ops/presales-excel-workflow.md`. Deployment was subsequently verified in the authorized continuation below; real-user value remains unverified.

## Authorized continuation — 2026-10-09

The user's subsequent approval extends this task to schema 0035 release/rollback preparation, guarded staging deployment when its preconditions pass, and one sourced public questionnaire file replay. The earlier deployment exclusion applies to the completed implementation phase only.

- [x] Fixed 0034-to-0035 expansion preserves original resources and reconciles unknown outcomes without downgrading history.
- [x] Image-only 0035 switching preserves configuration; legacy readers are refused whenever workbook history exists, before and after closing admission.
- [x] Actual PostgreSQL verifies migration, interrupted receipts, exact constraints, old-reader compatibility boundary and unchanged history.
- [x] Candidate source CI, signed artifacts, space guards and fresh staging state bind the release; application rollback retains schema 0035. Record any genuine blocker rather than bypassing it.
- [x] Freeze a sourced public questionnaire and a bounded row range before execution; import, generate, review and return the workbook, retaining original drafts and failures. No competitor-parity or user-time-savings claim.

Continuation completed: rc46/0035 released, actual rc45 rollback/reapply verified before workbook history existed. Six derived public SWU rows produced four drafts and two failures in nine calls, with four assisted reviews and a partial draft workbook export. This completes the bounded file-workflow task, not answer-quality or commercial acceptance; see `validation.md`.

## Authorized focused remediation

The user approved the three follow-up priorities. Preserve the completed rc46 replay and its failures. Add safe failure diagnostics, fix demonstrated Chinese keyword recall, and clarify same-scope support in the model instruction; do not add routes, retries, budget or platform features.

- [x] Chinese procurement requirements retrieve the matching clause through real PostgreSQL even when the controlled vector boundary contributes no match; tenant/version/active-generation filters and top-k limits remain.
- [x] Failed output records a fixed diagnostic category without raw model/source text, arbitrary exceptions or new schema; synchronous and durable executions preserve exactly-once accounting and normal fallback bounds.
- [x] Different edition/time/entitlement is explicitly excluded as same-scope counterevidence; existing missing-evidence fixtures preserve unknown. This verifies the instruction and controlled decoder contract only, not live model scope reasoning.
- [x] Prepare exact scoped candidate and checks; preserve historical outputs and state which reliability/semantic issues remain unproven.

Implementation checks passed; the bounded live diagnostic lost its control receipt and remains unknown, with at most three calls conservatively reserved and no rerun. Publication/CI receipt is retained centrally. The candidate is not deployed and actual quality remediation remains open; see `docs/ops/public-replay-remediation-20261009.md`.

## rc47 release continuation

The focused candidate was deployed on staging. Actual rc46 rollback/reapply, retained workbook history and deployed SWU03 keyword recall passed without new model/embedding calls. Historical quality failures and the unknown diagnostic remain. Human takeover for pending/failed rows was the next delivery gap at that checkpoint; the continuation below addresses it. See `docs/ops/rc47-remediation-release-20261009.md`.

## Authorized human completion continuation

Pending or terminal failed rows can receive a human-authored response with selected authorized literal evidence, then use ordinary review and original workbook/CSV delivery. Preserve original attempts and reviews. Explicit author/time and original human text survive refresh/export. No model, embedding, Job or quota dispatch occurs. Reject active generation, existing drafts, stale revisions and revoked sources. Same-key retries are idempotent. Keep the six-row public replay immutable and verify with owned fixtures. Human completion does not establish model quality or competitor parity.

## rc48 human delivery result — 2026-10-10

The authorized continuation is deployed on schema0036: exact signed rc48, actual rc47
rollback before human history, and rc48 reapply passed. A new labeled pending-row workbook
completed evidence selection, human save, separate review, reload and reviewed XLSX/CSV
through public HTTP with zero new model/embedding calls. Original failures and the frozen
six-row replay remain unchanged. With human history present, the real restore guard refuses
legacy readers before writes. The initial read-only-container harness failure is retained;
the corrected harness supplied a local workbook before any import. See `validation.md`
and `docs/ops/rc48-manual-release-validation.json`. Model quality and parent acceptance remain open.

## Evidence correction during review

The SWU03 replay exposed a delivery gap: review can correct response/prerequisite text,
but cannot replace irrelevant model-selected citations with the actual authorized clause.
Within the authorized evidence-quality remediation, allow an existing model or human
draft to be reviewed with a new explicit set of literal source passages. Preserve the
original draft/citations, every earlier review and all model attempts. Require an
explanatory note, exact authorization and independent review evidence snapshots; use
the corrected evidence consistently in current/history views and CSV. No provider,
embedding, ingestion, generation budget or frozen public-packet changes.

Acceptance: actual API/PostgreSQL round-trip with an initially wrong source, separate
review snapshots, idempotency/conflicts/revocation, no accounting changes; desktop/mobile
selection, prerequisite-link correction, reload/export and uncertain-save GET recovery.
Keep this capability distinct from improvement in original model accuracy. Additive
persistence and history-preserving migration guards precede any coordinated deployment.

## Authorized review citation release continuation

Extend the existing fixed expansion with0036-to-0037, then publish the coordinated
application through the existing supervised image window. Both windows must preserve
workbook/manual history and refuse reopening citation-incompatible readers once any
review owns its evidence, including an empty list. Reconcile uncertain migration receipts
without replay. Verify with owned PostgreSQL cases, exact signed sources and a fresh
staging preflight. Exercise rc48 rollback before new citation history, then use one new
labeled human-only fixture for correction, reload and export with zero model/embedding
calls. Frozen samples, failures and usage remain unchanged; parent acceptance stays open.

## rc49 citation delivery result — 2026-10-10

The continuation is deployed as signed rc49 / schema 0037. Expansion, actual rc48 rollback
before citation history, reapply and one new labeled human-only correction fixture passed.
Original draft/quote and earlier review remain separate from corrected review evidence;
reload and original XLSX/audit CSV delivery passed without model or embedding calls.
The actual guard now refuses rc48 restoration before writes because citation history exists.
Frozen public and prior human packets remain unchanged. See `validation.md` and
`docs/ops/rc49-citation-release-validation.json`; model quality and parent acceptance remain open.

## Generation failure diagnosis continuation

Retain the gateway's safe output diagnostic in new generation-only reports so schema,
quote, incomplete-output and transport failures remain distinguishable. Preserve original
failed outputs and historical report interpretation. Verify through the collector's HTTP
boundary with one dispatch per row and no added retry, route or runtime behavior.

Inspect at most one previously unexecuted public CQU requirement on the existing primary
route after freezing input, semantic criteria, current configuration and available daily
budget. This is a diagnostic, not another competitor comparison or a reliability rate.
Stop after its first outcome, including unknown; do not rerun SWU or the lost prior probe.

## Answer coverage and business-event meaning

The frozen v15 CQU03 response omitted testing arrangements and remediation, and assessed
a known necessity rule as unknown instead of assessing actual completion. Address these
observed semantics in model-facing instructions and field descriptions. Preserve all
decoder validators, historical outputs, public drafts, current routes and dispatch caps.

Verify existing protocol/persistence-policy contracts locally. One separately frozen,
previously unexecuted CQU01 requirement may probe the candidate on the current primary
route; stop after its first outcome. Reference criteria remain separate from inference.
Do not rerun CQU03 or treat a new single case as proof of its repair, a reliability rate,
customer acceptance or competitor parity. Deploy only after compatible exact-source
release checks and handling of accepted old prompt policies.

## Requested answer coverage distinct from business prerequisites

The rejected instruction-only candidate established the need for a separate answer
representation. Every requested question span must have an explicit answer and source
selection or information gap. Preserve the exact requirement text and reject missing,
repeated, overlapping or invented coverage. Business prerequisites remain independent
and require their existing literal source definitions; a reviewer request is not itself
a supplier obligation. A gap must remain an unanswered fact, not automatic noncompletion.

Keep existing saved drafts, human review and file export readable. Structural coverage
does not prove correct decomposition or semantic completeness; evaluate both explicitly.
Before promoting the new provider protocol, freeze one unexecuted CQU04 case and separate
criteria on the existing primary route, one request only, no retry or old-sample rerun.
Promotion requires passing that bounded semantic check plus affected protocol, historical
scorer, persistence and browser checks; public/competitive acceptance remains separate.

Result: the single CQU04 probe rejected v17 for an array/string schema violation
and generic, incomplete evidence requests. The tested private parser was preserved
centrally and withdrawn from the runtime checkout. No gateway/scorer integration
or deployment is eligible under this candidate; parent acceptance remains open.
See docs/ops/answer-coverage-protocol-20261010.md.

## Explicit provider output contract

The v17 failure exposed a separate transport-contract gap: JSON mode does not ask
the provider to enforce the schema. Add an opt-in strict JSON Schema request for
each existing presales route, using Pydantic's schema generation and the unchanged
business response model. All declared fields are required in the strict variant.
Keep JSON mode as the default and retain local shape, citation and semantic-rule
validators. Never silently retry or downgrade an unsupported strict request.

Bind strict execution to a distinct prompt/schema identity through existing frozen
policies, including restoration. Preserve saved drafts and historical scorer
contracts. Validate actual HTTP request/response boundaries and offline scoring.
Before enabling on staging, verify the current primary endpoint accepts one frozen
synthetic contract request within its existing daily budget. This can establish
observed schema compatibility only, not answer completeness or competitor parity.

Result: implementation and local/real-database checks pass. One actual primary
request conformed to JSON Schema but merged three business prerequisites and
failed the existing support-combination validator. Deliver the explicit capability
disabled by default; keep staging v15. Semantic quality remains unfulfilled.

## Mutually exclusive prerequisite evidence

The v18 response satisfied field types but violated a cross-field business support
invariant. Generate an explicit anyOf of the four valid evidence combinations for
new strict requests, rather than relying only on an after-generation validator.
Keep exactly the same wire field names and public projection; independent positive,
negative, missing and conflicting cases must all remain representable. Do not
discard unknown/conflicting cases merely to make the schema easier to satisfy.

Existing v18/run-v8 source interpretation and default v15 remain unchanged.
Validate a complete truth table against the JSON Schema and real decoder, including
the original support-combination failure, exact quotes, language and final limits.
The schema enforces combinations only: separate business-event decomposition and
entailment remain model judgments. No live request or staging enablement in this
implementation slice; offline evidence must establish the boundary first.

## Bounded v19 endpoint and meaning observation

Offline evidence is complete at d74df4e. Observe one fresh, explicitly synthetic
mixed-state requirement on the unchanged current primary route. Freeze input and
separate semantic criteria before inference; cover independent met, unmet, missing
and genuinely conflicting events rather than an easier missing-only public case.
Retain the first result, including failure/unknown, without retry or replay of any
old sample. Respect the live daily budget including direct-call reservations.
One observation may establish endpoint interaction and identify the next semantic
gap; it cannot establish a quality rate, public-task repair or competitor parity.
No staging enablement, route/model changes, embedding or deployment in this slice.

Result: the one request returned schema-valid v19 output in39.765s, but the original
gateway rejected support_quote. Two copied excerpts were rewritten with shared
context prefixes, and unregistered rehearsal status was incorrectly negative/none.
Four separate events and the actual authorization conflict were represented, but
the full frozen criteria failed. No retry or promotion; staging stays rc49/v15.

## Immutable evidence selection

Strict generation must select offered source spans for definition, positive,
negative and unconfirmed evidence without regenerating their text. Preserve exact
source context, qualifier text, public citation identity, legitimate four-way
support combinations and all existing business checks. Unknown/foreign span IDs
and legacy free-text quote objects must reject, never be repaired. Different calls
must not share mutable span state. No automatic semantic labeling of source text.

Default v15 and historical v18/v19 reports keep their original interpretation.
Version the new strict protocol and offline reports; validate real HTTP boundaries,
concurrent call isolation, historical scoring and database execution restoration.
This implementation makes no live provider/embedding calls and does not enable
staging. Preventing transcription does not prove evidence entailment, correct
unknown-vs-negative classification or competitor acceptance.

Implemented with54 focused tests,18 actual PostgreSQL cases and3,117 nonintegration
tests/23 subtests passing. V18/v19 schema modules and original failed scores remain
unchanged. No live v20 call or staging change; actual semantic acceptance stays open.

## Bounded v20 endpoint and meaning observation

Observe one fresh synthetic requirement through the unchanged primary route using
the committed v20 gateway. Freeze five independent prerequisite expectations and
separate reference criteria before inference: met, unmet, missing, conflict, and
completed training with an absent certificate; include another order as a scope
distractor. Require exact source selection, correct event direction, complete
state/next-action coverage and no invented obligation. Keep the first outcome,
including failure or unknown, with no retry or frozen-sample replay. Charge one
conservative direct reservation within the existing daily cap. This establishes
only one observed interaction, not public-task improvement or competitor parity.
No embedding, deployment, route/model change or automatic staging enablement.

Observed: one52.016-second request passed strict schema, exact span resolution and
the production decoder, but failed semantic acceptance. Verification status not
registered was still negative/none; four definitions selected current-state facts
instead of necessity rules; configuration and verification next actions were
omitted. Training and same-scope conflict were correct, and another order did not
fill the gap. Retain the accepted mechanical result and failed semantic review
separately; no retry or staging enablement. See the span-selection probe report.

## Evidence-role and per-item action candidate

S2001 shows that an accepted source selection can still use state facts as necessity
rules, classify an unregistered result as negative and omit requested actions.
Develop a separate candidate contract that first selects necessity rules, assesses
each rule exactly once and supplies a source-linked response for every exact part
of the requirement. Preserve explicit met, unmet, missing and conflicting states;
every outstanding item requires its own concrete action or confirmation question.
Completed items cannot acquire a new prerequisite action. Rule selection and state
selection must remain separately inspectable; no lexical semantic classifier,
source repair, extra route or extra model call may substitute for that distinction.

The public draft must retain each response, state summary and next action without
silent truncation, plus all required evidence and unknown-item questions. Validate
the standalone public parser with real source catalogs; preserve historical schema
bytes and v15/v20 runtime behavior. Controlled tests prove links/coverage/projection,
not source-role truth, entailment or semantic completeness. Do not integrate this
candidate into generation/scoring until a separately frozen real observation passes
its full semantic gate. No live call or staging change in this implementation slice.

Implemented as a standalone candidate with41 new cases,86 combined focused cases,
3,158 nonintegration tests and23 subtests passing. Raw-language, link, source and
public-size boundaries remain enforced. A wrong-role/missing-as-negative controlled
case remains explicitly wrong; no claim of semantic correction or runtime promotion.

## Candidate semantic observation

Freeze one new synthetic requirement with six independent prerequisites, including
unregistered verification versus explicitly required-but-unsubmitted paperwork,
completed training with an absent certificate, genuine conflict and another-order
facts. Use the committed candidate on the unchanged primary route exactly once,
with input/reference separation and conservative budget reservation. Assess every
necessity role, state, scope, requested response and action independently of schema
acceptance. Preserve the first outcome, including failure/unknown, without repair
or replay. No automatic runtime integration, staging enablement or competitor claim.

AR01 has now been observed once: HTTP 200 after 103.5 seconds, but candidate
projection and semantic acceptance both fail. It copies necessity rules into the
customer-question fields, treats an unregistered result as unmet, adds a training
certificate obligation and claims another order can fill current-order gaps.
Correct rule references and authorization conflict do not override those failures.
Do not integrate this candidate. The next investigation must distinguish protocol
overload from primary-model reasoning limitations before further product changes;
adding more selection fields is not evidence of semantic improvement. Preserve
AR01 and all earlier outcomes without replay or relaxed acceptance criteria.

## Minimal-contract discriminating observation

Observe one new six-prerequisite synthetic case on the same primary route with
one answer string instead of linked rules/assessments/responses. Preserve complete
source context, distinct met/unmet/missing/conflict states, absent training proof
versus actual unsubmitted paperwork, source roles, requested actions and another
order as a distractor. Freeze the input and all semantic criteria before inference.
Stop after the first outcome; no replay, retry, embedding or product integration.

This diagnostic changes output mechanics, not the meaning standard. It cannot pass
the structured product contract or establish causal attribution by itself: the new
sample and protocol differ from AR01. A pass permits further investigation of a
simpler design; a semantic failure shows nested schema is not required for errors
on this route and redirects work toward inference quality before more schema code.

MC01 returned one schema-valid answer in 32.0s, but only semantic criteria 6 and 9
passed. The answer still turns unrecorded verification into a completion task,
adds certificate submission after completed training, omits the overall enablement
decision and never identifies the necessity-rule source. Conflict and cross-order
exclusion are correct. Do not implement or promote this diagnostic contract. Errors
persist without nested output structure; investigate primary inference quality and
settings before any further schema expansion. The cause is not fully established.

## Prospective primary reasoning comparison

Freeze a new six-prerequisite sample and the same semantic standard for two planned
first observations on the existing primary endpoint: requested medium and low
reasoning. Both arms use exactly the same source input, prompt, model, output
format and limits; only reasoning_effort differs. Fix both requests and their order
before either dispatch. At most one request per arm, two total within the existing
daily cap. This is a prospective paired comparison, not a retry of MC01 or AR01.

Keep each original result, including failure or unknown, and stop remaining work
after a non-schema-valid first outcome or a changed live preflight. No embedding,
fallback route, production configuration or product integration. Evaluate all
criteria per arm before comparing them. One pair can reveal an observed contrast,
not establish reliability, statistical causality or competitor acceptance.

RC01 completed both planned first observations with identical input. Both requested
settings fail semantic acceptance: low calls the unregistered test result not passed;
medium asks to register a passing result without establishing it. Both invent a
training-transcript submission duty. Do not promote medium. A read-only catalog on
the same primary endpoint lists glm-5.3 alongside grok-4.7; separately qualify that
alternate primary candidate before further schema/effort tuning. Catalog presence
alone proves neither inference availability nor quality. Production remains unchanged.

## Alternate primary model qualification

Qualify the advertised glm-5.3 on the same primary endpoint with one new six-state
synthetic requirement and the existing full assessment-candidate contract. Preserve
rule/source roles, exact requirement coverage, all four states, per-item actions,
completed training without an extra proof duty and other-order exclusion. Freeze
input and independent criteria before one request; keep its first outcome without
retry, repair or historical replay. Keep primary low/streaming/120-second limits
and existing daily cap. No embedding, fallback or production setting changes.

Qualification requires matching requested/returned model identity, the unchanged
strict schema and resolver, and all semantic criteria. Catalog membership, accepted
HTTP or well-formed JSON alone is insufficient. A successful observation permits
integration planning, not deployment or competitor acceptance; a failed observation
must remain failed and cannot justify silently changing the product contract.

GP01 preserves all six correct states, necessity-rule links, both conflict sides,
completed training without a proof duty and other-order exclusion. Qualification
still fails: three responses each repeat the full requirement, the verification
action offers registering a pass without an explicit if-passed condition, and
requested/returned model aliases are not verified. Preserve the original failure.
A separate server-owned question-part identifier candidate will address mechanical
coverage only; it cannot establish semantic action safety or model qualification.

## Server-owned question coverage candidate

Remove the requirement-text transcription task from prospective candidate outputs.
Every offered question part must receive exactly one ordered response; omissions,
duplicates and foreign IDs fail. Preserve the full original question, source context,
state/action obligations and existing public limits. No inferred semantic coverage,
GP01 output repair, deployment or extra live request belongs to this implementation.

## Prospective question-part observation

Observe the frozen question-assessment-candidate.v1 once on the same primary
endpoint with glm-5.3 and a new reordered six-state synthetic case. Freeze input
and independent criteria before dispatch; preserve the first result, including
failed/unknown. Require every offered question ID, real rule evidence, all six
correct states, neutral unknown-state actions, no added proof duty and cross-order
exclusion. No prompt change, extra request, fallback, embedding or production switch.
A mechanical pass does not establish substantive answer completeness.

QP01 passes all 11 frozen mechanical/semantic criteria in assistant review; no
reliability or independent approval is established. Advance to a separate bounded
public-task observation: CQU02 performance and CQU05 team qualification from the
historical December 2024 Chongqing University procurement. Current official fetch
binds exact buyer clauses. No supplier performance/personnel evidence is provided;
the correct deliverable is a precise evidence-gap response, not a compliance claim.
Freeze both inputs and separate references before at most one request per case.
Stop the remaining case after failure/unknown; no retries or model/prompt change.

Public outcomes: both original generations succeed, but neither fully satisfies
its frozen semantic checklist. CQU02 correctly reports absent performance evidence,
but omits resource/measurement-method details from the follow-up. CQU05 treats
general work experience as an alternative to specifically required software project
management experience. Preserve both successes and semantic failures. Investigate
a separate source-grounded review step for altered qualification/category/scope;
do not promote, expand schemas again or repeat generation until success.

## User-directed Grok priority and GPT comparison — 2026-10-10

The user explicitly authorizes testing `gpt-6-luna` through the newly supplied
`FALLBACK_PROVIDER_NAME2=gpt` channel, while keeping `grok-4.7` semantic correctness
as the main objective. Use the same known CQU02, CQU05 and QP01 regression inputs
for both channels; do not call this unseen evaluation or rewrite prior results.
Separate substantive errors (facts, obligations, scope, state or required-answer
omissions) from optional clarification detail. CQU02 resource/measurement detail
is an improvement item, not by itself a rejection of useful draft generation.

Bound this continuation to six comparison calls and, only if Grok shows substantive
errors, one versioned correction with at most three further Grok regression calls.
No automatic retry, repeated sampling until success, added review-model dependency,
embedding or production model switch. Preserve every outcome and provide reviewable
answers. Human-reviewed file delivery remains useful while automatic quality improves.

Result: the six comparison calls and three revised-Grok calls are complete. GPT
returns three valid drafts with correct central uncertainty/state handling and
documented wording limitations. Grok baseline has one undecodable stream and two
substantive failures; one prompt correction retains rule-as-fact/unknown-as-unmet
and adds a rejected zero-based-index error. This continuation does not achieve Grok
semantic acceptance. Stop at the frozen cap; no candidate or route is promoted.
See `docs/ops/grok-gpt-semantic-comparison-20261010.md` and retained original answers.
