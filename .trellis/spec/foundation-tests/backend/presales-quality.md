# Deployed presales quality evaluation

## Scope and entry points

`scripts/evaluate_presales_quality.py` separates live `run` from offline `score`.
The input fixture is `evaluation/presales_quality_v1.json`; gold is a separate file
bound to its exact SHA-256. Run never opens gold. Both use existing Presales API
schemas rather than creating another draft contract.
Git attributes preserve the exact fixture/response/prompt bytes, including CRLF;
the runner uses LF so its recorded hash is stable across checkout settings.

## Signatures

`python -m scripts.evaluate_presales_quality run --base-url HTTPS_ORIGIN
--object-host HOST --repeats 1|2 --output NEW_FILE` reads only `--input`.
`score --run RUN_FILE --output NEW_FILE` also reads `--gold` and makes no requests.
`collect(...)` permits separate injected control/object transports for tests;
`score(dataset_path, gold_path, report)` returns a reproducible mechanical report.

## Observable contract

- One or two fresh public demo sessions; at most six requirements each. Sequential
  generation, no model retry/failover, no administrator identity or modified quotas.
- Use normal multipart upload and document readiness before packet creation.
  The signed object PUT has a separate client without cookie or CSRF credentials;
  HTTPS host allowlisting and no redirects apply.
- Refuse an existing report before any request. Save each attempted row before and
  after dispatch, then retrieve its persisted result. Preserve failures. Unknown
  running outcomes or admission rejection stop collection without a new generation.
- Logout in finally; existing product expiry cleanup owns temporary demo tenants.
  Do not write cookies, CSRF, signatures or arbitrary exception/HTTP bodies to disk.
- Offline scoring verifies dataset/gold hashes, exact requirements, source content
  hashes and applicability, then checks labels, original-text citations and required
  quote anchors. Missing planned rounds/rows remain in the denominator.
- Missing usage and unverified currency cost are null. Latency includes failed HTTP
  attempts. Keep assistant semantic review distinct from mechanical scores and from
  business approval. Never infer customer accuracy or SLOs from this short corpus.

## Tests

`tests/evaluation/test_presales_quality.py` uses HTTP MockTransport for browser API
and object-store boundaries; no external requests. Assert no automatic retry after
an uncertain response, credential separation, original input isolation, overwrite
protection, incorrect affirmative labels, fabricated quotes, conflict-side omission,
hash/source substitution rejection and unknown usage. These tests are not live
model observations. Live reports are explicit bounded runs outside normal CI.

## Validation and error matrix

| Condition | Result |
|---|---|
| Existing output | FileExistsError before network; preserve file |
| Wrong input/gold hash or source content | Reject offline scoring |
| HTTP 401/403/429 or unresolved running attempt | Stop, save partial result, logout |
| Timeout/502 followed by terminal row | Retain HTTP failure plus persisted outcome |
| Failed or missing planned row | Keep it in the score denominator |
| Absent token usage or price | null; never manufacture zero cost |

## Good, base and bad cases

Good: two independent demo enterprises with frozen inputs and separately reviewed
original drafts. Base: MockTransport verifies collector behavior without model
quality claims. Bad: retry until success and omit failed attempts from statistics.

## Wrong versus correct

Wrong: reuse a logged-out demo actor for a prompt comparison and score its empty
retrieval as model performance. Correct: keep a newly owned diagnostic session
active until its reads finish; assert nonempty evidence before model dispatch.
Logout retires access through the existing cleanup lifecycle. Never reactivate a
retired tenant or weaken authorization for a test. A read-only terminal snapshot
may reconcile explicitly owned attempt IDs, preserving the original HTTP report.

## Citation protocol and fresh holdout

`presales.v3` changes only the model-facing citation contract. HTTP fixtures must
return offered `citationId` selections; public saved drafts continue to carry exact
excerpts and source metadata. Unknown/duplicate/cross-request selections, legacy
quote fields and same-version conflicts are negative cases. Do not relax final
substring/tenant/version validation to make model responses pass.

The separate `evaluation/presales_quality_holdout_v1.json` and `.gold.json` contain
six new fictional H1 requirements frozen before their first model request. Use
explicit `--input` and `--gold` paths with the existing evaluator; the default C1
baseline and all its failures remain immutable. This is new assistant-authored
material, not independent expert adjudication or customer validation. No prompt
tuning on its results may be reported as a holdout measurement.

Keep model-protocol/real-database/browser correctness separate from live semantic
quality. A rejected reference or classifier error remains a failure in the planned
denominator. Unknown billing remains null.

## Prerequisites and generation-only trials

`presales.v4` fixtures explicitly return `prerequisites: []` when none apply.
Use the real gateway at the HTTP boundary to verify unmet/unknown prerequisites
cannot accompany supported, conditional output retains every outstanding condition,
and prerequisite-only citation selections resolve without requiring duplicate
top-level selections. Wrong: deleting valid citations to satisfy the adapter.
Correct: resolve the ordered union of explicit references with the same catalog.

`python -m scripts.evaluate_presales_gateway --input <dataset.json> --output
<new-run.json> --provider-env <local-env> [--model-route primary|fallback]` makes one
call per requirement, at most six, using the explicitly selected route and a
120-second deadline. The default remains fallback. It never reads
gold or writes tenants. Sources must each fit the 1800-character evidence bound.
Existing output files fail before dispatch; raw bounded responses and usage survive
invalid draft/schema results. Secrets/headers and non-200 bodies are not recorded.

These `presales-gateway-run-v1` reports are generation-only observations with complete
synthetic sources, not public retrieval/persistence results or equivalents of the
public evaluator. H1 is now a known regression corpus. Freeze H2 and its gold hash
before first inference; retain initial failures separately from deterministic decoder
replay. Never present a replay as an extra successful live inference or independent
adjudication. Inspect meaning/conditions in addition to labels and exact quotations.

## Official public-source fixtures

`presales-public-quality-input-v1` is a separate input format with `synthetic=false`.
Each source must have exactly one publication identity: HTTPS URL without credentials
or query, timezone-aware retrieval time, extracted-page/receipt/excerpt SHA-256.
The loader binds the excerpt hash to the parsed source text and rejects incomplete
coverage. The collector still never reads gold; classification, quotes and all
failed/unattempted questions use the existing scorer. Reports must not label public
material synthetic or imply that assistant-authored scenarios are customer facts.

`evaluation/presales_public_r2_v1.*` freezes limited official excerpts and five
assistant-authored requirements before inference. Check unmet cache purge separately
from unknown jurisdiction/endpoint configuration. Public product documentation is
not proof of actual customer configuration or end-to-end disaster-recovery guarantees.
Page/receipt membership is verified when freezing; the loader checks bindings, not
the truth of a remote page. Do not rewrite old synthetic fixtures, runs or scores.

The first public R2 trial exposed a fixture-contract mismatch: R2-R3 put a current
execution fact only in the requirement hypothesis, while `presales.v8` requires
source evidence for prerequisite states. Preserve the original unmet/unknown
mismatch and count the item outside accepted semantics; do not change gold after
inference or call a prompt-compliant response a demonstrated model defect. Before
another trial, separate public capability evidence, hypothetical scenarios and
project records explicitly. Synthetic project records must not claim official
publication provenance or independent customer approval.

## Ordered decision regression and original-outcome scoring

`presales.v5` explicitly orders unresolved source conflict, direct counterevidence,
proof shortage, established enabling conditions, and full support. H1/H2 are known
regressions; H3 and its separate gold are frozen before v5 calls. Include contrasting
absent-vs-explicitly-unobtained certificates and limited-vs-no-priority clauses.
Do not change gold after seeing outputs. A timeout is retained in the full planned
denominator; unknown token usage for that attempt makes the aggregate unknown.

`python -m scripts.score_presales_gateway --input <input> --gold <gold> --run <run>
--output <new-score>` scores original `presales-gateway-run-v1` results offline.
It checks dataset/gold hashes, unique planned rows, requirement text, version/hash/
scope bindings, evidence text, and citation identity against the actual offered
fragments. Rejected drafts do not become successes by decoding their raw responses.
All observed requests, including rejected output, contribute usage when known.

Wrong: count only accepted drafts or present deterministic replay as first-attempt
success. Correct: keep rejection and semantic errors in the denominator, check
both exact quotations and meaning, and record unverified billing as null. Tests
use the frozen failed v4 run to guard this distinction and reject tampered bindings.

The v5 trial demonstrates why label and citation success are insufficient. H3-R3
selected both conflict sides with the expected label, but inverted which source
agreed with the requirement in its answer. H3-R5 labeled an unrecorded acceptance
state as unmet instead of unknown. Review these propositions and prerequisite
states explicitly; do not infer semantic correctness from a valid schema or label.
Clarifications must ask for missing facts or revised terms, not facts already stated
by the cited sources (H1-R4). Preserve required-anchor gaps even when another source
contains a similar fact (H1-R2); changing gold after inference hides the observation.

## Semantic grounding and transport diagnostics

The v6 candidate keeps the selection schema unchanged. H1/H2/H3 are known regressions;
H4 is frozen separately before its first use and remains assistant-authored material.
Review every prerequisite state and the answer's source-to-requirement relation,
not only final classification. Predeclare dataset order, request cap and stop rules;
an unattempted gated dataset is not a successful test. Never silently continue sampling
after a declared stop or describe assistant review as independent adjudication.

`RecordingTransport` adds optional `transportFailure: {type, phase}` to synthetic
generation reports. Types are fixed HTTPX library names or HTTPError; cancellations
use CancelledError. Phases distinguish awaiting_response_headers from
reading_response_body (including stream cleanup). Exception messages, URLs, headers,
partial response bodies and credentials are excluded. A received HTTP 200 followed
by a body read failure is still a failed generation with unknown usage. Always close
received streams; rethrow rather than repairing or retrying. External cancellation
leaves the row/run interrupted and saves the trace; the gateway's own deadline still
maps to its existing presales_model_timeout. These client observations cannot identify
which network intermediary failed or whether the provider completed and billed work.

Public `collect()` boundary tests cover ConnectError redaction/no retry, partial 200
body failure/stream closure and interrupted collection. They test diagnostics, not
model semantics; frozen raw v5 failures provide the actual semantic regression cases.

New generation-only observations also retain optional `errorDiagnostic` from the
gateway's allowlisted `PresalesError.diagnostic_code`. Omit it when unavailable,
including ordinary transport failures; never infer it from exception text. Preserve
`state=failed`, the original errorCode, bounded traces and one dispatch per row.
This is additive to run-v6, not a new decoder or permission to rewrite old reports.
`test_presales_gateway.py` verifies actual HTTP-to-report propagation, absent transport
categories, credential exclusion and refusal to overwrite the original report.

## Projected input reports (v7)

The initial v7 collector wrote `presales-gateway-run-v2`. Each observation's `sourceInput`
is the complete synthetic internal GenerationInput actually passed to the gateway;
`traces[].input` is still exactly the JSON sent to the provider, now SelectionInput.
Never inject hidden UUIDs into a recorded wire request for scorer convenience.

The offline scorer supports historical run-v1 unchanged. For run-v2, it checks the
deterministic dataset-to-source bindings (including UUIDs, full content, filenames,
scope and versions), then verifies every projected fragment, display label and
request-local citation ID against that input. It checks that an accepted draft
matches its original selected references and projected conditions. Failed rows stay
failed even when their raw response could be decoded; no replay becomes a success.
Tests mutate internal source IDs, source scope, source labels, fragment text,
references, fragment coverage, accepted conditions and raw output to verify rejection.

The structured prerequisite-review candidate writes `presales-gateway-run-v3`.
Its result must retain every prerequisite state and its zero-based citation indexes;
the scorer compares the complete resolved result to the recorded draft. Missing/null
states, changed state (including unknown to unmet), changed links or flat projection
tampering fail scoring. Historical run-v2 reproduces its original flat projection
only; v1/v2 results cannot acquire new assessments. Frozen v5/v6/v7 scores are
regression-tested unchanged, including rejected or unattempted rows. Do not rewrite
run or score files or claim that application state preservation fixes model semantics.

Run the real PostgreSQL and both presales browser suites when changing this protocol:
wire fixtures must consume SelectionInput, not GenerationInput. A fixture cannot
recover internal chunk/version IDs from provider input; check those identities in
saved public evidence and the actual database chunks instead. Browser model-call
records describe only the offered reference/source and retain the dispatch count.

## Source-bound prerequisite regression review

1. **Scope / trigger:** a correct conditional label and exact citations can hide an
   unknown-to-unmet error. `scripts/score_presales_prerequisites.py` checks the
   prerequisite states and their own evidence separately from the original scores.
   It never dispatches HTTP or grants full semantic/production acceptance.
2. **Signature:** `score_prerequisites(dataset_path, gold_path, run_path,
   expectations_path, review_path) -> dict`; CLI requires `--input --gold --run
   --expectations --review --output`. Output creation is exclusive. Exit 1 after
   saving a valid failing score; exit 0 only when all prerequisite rows pass.
   Always inspect the original classification/citation score and full answer too.
3. **Contracts:** `presales-prerequisite-gold-v1` binds dataset SHA and all row keys;
   each expected prerequisite has key, description, state and source/excerpt anchors.
   `presales-prerequisite-review-v1` binds exact run/expectation bytes by SHA, names
   the reviewer/type and explicitly maps expected keys to zero-based output indexes
   with a reason. Null means an omitted prerequisite. The reviewer, not a keyword
   heuristic, establishes semantic correspondence. Accepted v2/v3 original outputs
   are validated with the existing scorer before reading prerequisite states; failed
   responses are never reinterpreted as successes. Old scores remain byte-for-byte
   unchanged. The run is bounded to 2 MiB; reference and review files to 256 KiB.
4. **Validation / errors:** stale hashes, wrong dataset coverage, duplicate keys or
   indexes, unknown/invalid anchors, out-of-range/non-integer indexes, incomplete
   mappings marked reviewed, and reviewing unavailable results raise ValueError.
   Missing reviews, absent/extra prerequisites, state or per-item anchor mismatch
   yield a failing score. Every planned row remains in the denominator, including
   failures and unattempted rows. Existing output is never overwritten.
5. **Good / base / bad:** good: explicit mappings survive changed order and paraphrase;
   base: controlled HTTP outputs verify the checker, not model quality; bad: overall
   citations contain an anchor but the relevant prerequisite does not cite it.
   `semanticReviewRequired=true` and `independentDomainReview=false` remain explicit,
   even with a human-typed reviewer field: the file is not identity attestation.
6. **Tests:** `test_presales_prerequisites.py` reproduces the immutable v7 Windhub
   H3-R5 failure, verifies source/state checks, strict bindings, complete denominators,
   explicit omissions, tampered mapping rejection, CLI exit and overwrite refusal.
   Its controlled v3 run proves no matching relies on prose or array order.
7. **Wrong vs correct:** wrong: edit the original gold or call an after-the-fact
   reference a holdout. Correct: add an explicitly labeled H3 regression supplement,
   retain the original input/gold/runs/scores, and freeze new candidate criteria
   before the next inference. Prerequisite checks alone cannot approve prose.

The frozen v8 candidate changes the order of assessment in the prompt and schema
display only; the field contract is unchanged. A batch plan specifies exact hashes,
route/model, six single attempts, 120-second deadlines and no failover/retry. Stop
after the batch. Other datasets, additional paid requests and deployment require
their own applicable authorization; local checks do not satisfy those boundaries.

The authorized v8 H3 batch has six original successful outputs with matching labels,
anchors, prerequisites and assistant prose review. Preserve this known-regression
run alongside the failed v7 run, and regression-test both recorded scores. Do not
call it a holdout or independent sign-off: no H4/H1/H2 request was made in that batch.
The slowest call took 111.188 seconds and reported completion tokens can exceed the
requested 4000, so neither six passes nor max_tokens establish a latency or cost SLO.

## Explicit alternate-channel trials

`load_route_settings(provider_env, model_route)` selects BASE_URL/API_KEY/MODEL_NAME
for primary and the FALLBACK_ fields for fallback; unselected credentials need not
be present. It does not rewrite the file or swap route names. The collector records
selectedRoute and configuredModelName, passes the explicit route to the same gateway
and preserves single attempts, the deadline and unknown usage. Tests inspect actual
HTTP URL, authorization, model and dispatch count for each route, including failures.

Identify the route actually in use before interpreting "backup": presales can already
be using configured fallback, making primary the available alternate. Freeze the new
channel/model with the unchanged prompt and cases, keep original-channel failures,
and retain the semantic release gate. A new channel's result is a separate experiment,
not a retry that makes the original attempt successful. Evaluation never switches
routes automatically. Product background recovery is separately gated as below.

When the user updates the local file, identify the selected endpoint, model and
protocol again; a stale PROVIDER_NAME label does not select the route. Create a new
freeze and output path for a different designated channel. Preserve an interrupted
candidate's original bytes and record cancellation uncertainty separately. A 200
model catalog listing proves discovery only; a generation 200 without the standard
choices envelope is still an invalid response, not a draft or semantic score.

## Background recovery fault injection

`tests/presales/test_presales_background_integration.py` uses real PostgreSQL,
ASGI, Job leases, commercial reservations and demo limits with controlled HTTP
transport. Verify admission without inference, idempotency, 503/timeout/200-error
failover, terminal output failures, two-route exhaustion, unknown outcomes across
restart, stale fencing, expired observed usage, pre-HTTP not_sent accounting,
authorization/source changes, cancellation, daily budgets and one half-open probe.
Batch tests must continue after per-row rejection and never create duplicate work
on same-key replay. Migration tests cover empty upgrade/downgrade and refusal once
dispatch history would be lost.

The dedicated background browser harness exercises the real API/database/worker
coordinator with synthetic sources and a controlled model. Verify page navigation,
refresh, partial completion, failed-only retry, quota settlement/release, tenant
isolation and mobile layout. Keep legacy synchronous, review and CSV suites passing.
No live provider is required for these tests. Never alter frozen runs/gold, hide
original failures, or count fault-injection success as a semantic release pass.

For isolated migration validation, create a uniquely owned PostgreSQL schema and
an empty alembic_version table in that schema **before** upgrading. With
search_path=temporary_schema,public, Alembic can otherwise discover the public
version table and apply incremental ALTERs to public tables. Assert both schema
versions and table ownership before tests, verify public is unchanged afterward,
and remove only the exact owned schema in finally. Do not point a harness at an
empty schema and assume search_path alone provides migration isolation.

## Strict request collection and historical scoring

`collect(..., strict_output=True)` or the explicit `--strict-output` option selects
strict schema only for the requested route. It emits presales-gateway-run-v10 and
records the exact responseFormat in each dispatched trace; defaults remain run-v6.
run-v7 belongs to the rejected answer-aspect experiment and remains unsupported.

Both scorers retain v8 StrictBasisDraft and v9 ConstrainedBasisDraft branches, and
add v10's SpanBasisDraft/resolve_span_basis. Gateway scoring verifies the version-specific recorded
strict response format before processing success or failure, then
binds accepted original output through the unchanged basis projection. Missing
required arrays cannot be silently reconstructed. Legacy v1..v6 keep their existing
parsers and frozen scores; do not use newer schema versions to reclassify a rejected
historical output. Relabeling old request formats as a newer version is refused.

Run-v10 records spans in the provider input. `bind_projected_input(..., spans=True)`
first binds the original full evidence/source projection, then reconstructs the
complete span list with the same deterministic offering function. Reject changed
text, IDs, parent references, ordering, duplicates or omissions before evaluating
even a failed observation. Older versions use their original input parser. Both
scorers resolve accepted v10 selections through the same core decoder; do not
rebuild quote materialization or semantic inference in scripts.

HTTP-boundary evaluation tests cover successful and failed strict outputs, altered
request-format rejection, strict omitted-field rejection and v6/v8 compatibility.
The single 2026-10-10 v18 synthetic observation accepted the request and conformed to
JSON Schema but failed support_combination. Preserve that failed state and semantic
review. It proves one observed response format only, not constrained decoding,
reliability, public-task improvement or permission to enable a production route.

v19 development made no provider calls. Offline validation of the original v18
bytes confirms its contradictory combination is outside the new schema, while the
original v8 score and failed observation remain identical. Unit truth tables and
controlled HTTP prove shape contracts, not future model interpretation or quality.

The separately frozen S1901 v19 observation returned HTTP200 and valid anyOf
branches, but failed support_quote and semantic review. Two quotations reconstructed
shared date/order prefixes instead of selecting literal spans. The model also put
"pass status unregistered" in negative/none, although its prose called that state
unknown. Preserve the original failed report: legal support combinations, four
separate propositions and a correct conflict do not make the whole draft valid.
Review must compare structured direction, prose and original evidence separately.
Never repair a quote or change missing to unmet to obtain an accepted draft. Future
immutable evidence selection would address transcription only; it would still need
independent missing-vs-negative and entailment criteria. See the bounded diagnostic
record in docs/ops/presales-support-contract-probe-20261010.md.

V20 development makes zero provider calls. Controlled HTTP tests cover correct
selection, cross-call IDs and byte-limit refusal; real database tests preserve
selected evidence and one charge through admission/restoration. Offline checks
retain the exact v18/v19 failed reports and original scores. The old v19 generated
quote objects are invalid v20 selections, not inputs to a quote repair. A valid
span can still be selected in the wrong semantic direction, so frozen criteria
must continue assessing missing-vs-negative, event decomposition and entailment.

S2001's first v20 observation passes JSON Schema, span resolution and the original
decoder, but fails semantic review: a genuine unregistered result is negative/none,
four definition references point to state records rather than necessity rules,
and requested next actions are missing. `acceptedDraft`, `statusMatch` and complete
required-source coverage therefore do not constitute semantic acceptance. Compare
each definition's role, support direction, prose and requested action separately.
Preserve the accepted mechanical report and rejected semantic review without
rewriting either. The new record is in docs/ops/presales-span-selection-probe-20261010.md;
do not promote strict mode or repeat frozen samples from these checks alone.

The standalone assessment candidate uses `resolve_assessment(content, requirement,
catalog)` before any runtime integration. Verify every necessity rule is assessed
exactly once, each requirement substring is covered in order, each answer retains
its own information gap and every unmet/unknown item has its own action. Met cannot
add an action. Reuse literal span resolution and validate raw prose before labels;
reject final-size overflow. Keep a test where a wrong evidence role and missing-as-
negative selection remain wrong: separate arrays, exact coverage and valid actions
do not prove source entailment or semantic completeness. A combined rule/state
passage must remain legal. Candidate schema identity is separate from gateway v20;
no historical scorer or frozen policy may silently start using the new resolver.
