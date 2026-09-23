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

## Projected input reports (v7)

The collector now writes `presales-gateway-run-v2`. Each observation's `sourceInput`
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

Run the real PostgreSQL and both presales browser suites when changing this protocol:
wire fixtures must consume SelectionInput, not GenerationInput. A fixture cannot
recover internal chunk/version IDs from provider input; check those identities in
saved public evidence and the actual database chunks instead. Browser model-call
records describe only the offered reference/source and retain the dispatch count.

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
not a retry that makes the original attempt successful. No automatic route switching
is added to evaluation or the product.
