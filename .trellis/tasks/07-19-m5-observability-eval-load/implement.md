# M5 Observability Evaluation And Load: TDD Implementation Plan

## Preconditions

- M0-M3 are archived and M4 implementation remains green in the current working tree.
- M5 work must not convert M4 working-tree evidence into reviewed evidence.
- Local deterministic results and workstation load results are labeled with their exact
  scope and never promoted to production claims.

## Slice 0: Measurement Contracts And Trellis Task

- [x] Add M5 PRD, design, implementation plan, task metadata, and manual-gate rules.
- [x] Define metrics, load report, fault result, and evaluation result schemas.
- [x] Add redaction, finite-label, percentile, and report validation tests.

Rollback point: contracts and tests only; no runtime behavior changes.

## Slice 1: API And Worker Prometheus Metrics

- [x] Add an explicit process-local metrics registry and typed collectors.
- [x] Expose Prometheus-compatible `/metrics` from API and Worker probe apps.
- [x] Instrument API route-template latency/error, Worker job outcomes, and Outbox
  publication with bounded labels.
- [x] Prove repeated app factories do not produce duplicate collectors and metrics never
  contain raw identifiers, secrets, signed URLs, or bodies.

Rollback point: removing metrics injection leaves business behavior unchanged.

## Slice 2: Deterministic Fault Injection

- [x] Add default-off local/test-only settings and validation.
- [x] Add deterministic handler, model, MCP, and object-store adapter decorators.
- [x] Add retry, terminal, lease, and zero-duplicate-side-effect tests.

Rollback point: disabled configuration builds the original adapters.

## Slice 3: Unified Evaluation Contract

- [x] Add M5 dataset and report schemas that index M3 retrieval and M4 safety/Agent runs.
- [x] Report dataset and behavior-version hashes, aggregate quality, zero-tolerance
  safety results, and explicit deterministic-provider limitations.
- [x] Keep real-provider evaluation as a separate external manual gate.

Rollback point: M3 and M4 evaluator entry points remain unchanged.

## Slice 4: Reproducible Local Load Runner

- [x] Add bounded-concurrency health, create/status, duplicate-idempotency, and optional
  end-to-end scenarios using real HTTP endpoints.
- [x] Emit deterministic JSON/Markdown with P50/P95/P99, errors, throughput, environment,
  sample count, saturation inputs, bottleneck, and capacity conclusion.
- [x] Add offline unit tests for percentile and report contracts.

Rollback point: load tooling is additive and test data is explicitly cleanable.

## Slice 5: Fault And Recovery Procedures

- [x] Record local procedures for worker termination/lease reclaim, Redis outage,
  model timeout, and MinIO failure/recovery; the automated Redis/MinIO drill is explicitly
  readiness-only and does not claim Outbox republish or object-content reconciliation.
- [x] Assert authoritative PostgreSQL state and no duplicate effective side effects through the
  existing Job/Agent integration contracts and explicit worker-lease procedure.
- [x] Preserve unavailable representative infrastructure as `blocked_external`.

Rollback point: procedures are local/test-only and include cleanup.

## Slice 6: Local Observability Stack

- [x] Add version-pinned Prometheus and Grafana configuration under an optional Compose profile.
- [x] Add a dashboard for API RED, Outbox, Worker jobs, retries, and dependency health.
- [x] Verify telemetry outage is fail-open and document process-scrape topology.

Rollback point: observability services are optional and independent of readiness.

## Slice 7: Evidence And Documentation

- [x] Run the available focused and regression gates, evaluators, the 100-request health
  baseline, the 1000-request dependency-inclusive ready load with host/API resource
  sampling, guarded
  Redis/MinIO outage-recovery drills, and observability profile provisioning checks;
  production failover and RTO/RPO remain external gates.
- [x] Save sanitized artifacts and SHA-256 hashes under `evidence/m5/`.
- [x] Add working-tree manifests, manual gates, evidence index entries, README/spec updates,
  and code-backed interview Q&A.
- [x] Separate reviewed implementation and evidence commits when the user approves the
  commit boundary.

## Slice 8: Protected Staging Quality Execution

- [x] Add a manual, Environment-protected `trial`/`full` v2 RAG evaluation workflow that
  serializes with staging deployment and rollback, has no Kubernetes credentials, and
  uploads only the sealed report.
- [x] Bind artifact upload to the current run and attempt, with a regression check
  rejecting directory-wide uploads and a per-workflow Actionlint pass.
- [x] Link the 4C4G runbook to exact prerequisites, PowerShell dispatch/download
  commands, report verification, and failure-handling instructions.
- [x] Audit live GitHub prerequisites read-only, including paginated Environment
  variables, runner state, workflow publication and actual protection rules.
- [x] Apply the reviewed repository/Environment protection scope and publish the
  local workflow after owner authorization. The 2026-09-05 API observation found
  an unprotected `staging` Environment in the now-public repository.

Validation on 2026-09-05: 1008 non-integration tests passed (125 deselected), Ruff
format/lint and mypy passed, and `rhysd/actionlint:1.7.7` passed for the evaluation
workflow. Offline v2 selection is 12 trial cases and 40 full cases, with unchanged
dataset/corpus hashes. Four PowerShell examples parsed; the documented verifier
accepted a valid seal and rejected a mismatched evaluator SHA. No live evaluation ran.

- [x] Execute the authorized single 12-case trial and preserve its terminal result:
  run `33976542098`, attempt 1, timed out during frozen dependency sync; the evaluator
  was skipped and no quality report was produced. Do not redispatch or count this as
  model-quality evidence.
- [x] Bound dependency setup to five minutes, omit development dependencies, select
  the pre-provisioned Python, and disable implicit sync in the token-bearing step.
  Test the workflow contract red-to-green and validate both dataset selections in an
  isolated runtime-only environment without any staging/provider calls.
- [x] Record the applied protections, published evaluator SHA, passing CI and failed
  trial in append-only evidence, keeping historical full-suite results and gates intact.

Validation on 2026-09-06: startup and evidence contract regressions failed for the
expected missing controls/record, then passed after implementation. An isolated Windows
runtime-only environment installed 110 locked packages offline, excluded Ruff/mypy, and
passed both 12/40 dataset selections with unchanged hashes. This does not validate the
Linux runner's cold-download throughput. Prepare that runner before a new separately
authorized trial; do not retry the failed run to select a passing result.

- [ ] Dispatch a clean full 40-case execution only after stable provider revision,
  billing metadata, approved representative corpus scope, and independent human semantic
  reviewer are available. A workflow pass alone is not a completion claim.

## Slice 9: Reuse The Prepared Linux Evaluator Runtime

- [x] Add a regression requiring one job-level `UV_PROJECT_ENVIRONMENT` outside
  checkout, a Python executable preflight before sync, and default checkout cleanup.
  The regression failed because the workflow had no persistent environment setting.
- [x] Point frozen sync and no-sync evaluation at the prepared runtime; retain the
  five-minute setup ceiling, secret scope, lockfile, and shared staging concurrency.
- [x] Verify the existing Linux environment offline, including local workspace package
  rebuilds, unchanged 12/40 selections, and report payload integrity. Make no model calls.
- [x] Append runtime-preparation evidence and operator/spec guidance, distinguishing a
  prepared environment from a complete cold registry cache and from real quality results.
- [x] Run focused and non-integration checks, Ruff, mypy, Actionlint and diff validation.
- [x] Obtain the owner's commit confirmation. Publication and a new real trial remain
  separately authorized actions; do not repeat trial `33976542098`.

Rollback point: revert the workflow environment selection; retain the prepared runtime
and wheelhouse for diagnosis. Do not alter deployed application images or old evidence.

Validation on 2026-09-07: the workflow contract suite passed 11 tests. A new preparation
index contract failed for the missing record, then passed after the append-only record
was added. On Linux, all four workspace packages rebuilt offline (479 ms preparation),
frozen sync checked 109 packages without changes, and both 12/40 selections passed payload
and corpus checks. This used runner checkout `5bd1e6d`; scoped runtime inputs match
`a1255e8`. No new real trial or model call ran, and publication remains pending.
Final checks: 33 focused tests and 1012 non-integration tests passed (125 integration
tests deselected); Ruff format/lint, mypy (161 source files), Actionlint, Trellis context
validation and `git diff --check` passed. No application integration or browser run was
needed for this workflow/evidence-only change.

## Slice 10: Authorized Prepared-Runtime Trial

- [x] Publish `c1e2ec7` after owner authorization and verify both jobs in Quality run
  `34142156900` passed against that exact commit.
- [x] Recheck Environment refs, host allowlists, the idle 4C4G runner, unchanged locked
  runtime, twelve-case selection and live application image identities.
- [x] Validate the unique existing active synthetic smoke owner, issue an eight-hour
  token through the reviewed issuer, verify its loopback API session using the typed
  `SessionResponse`, and update only the protected Environment secret through stdin.
- [x] Dispatch one newly authorized `trial` run `34143634860`, attempt 1, at evaluator
  commit `c1e2ec7a6bb80da8fc68dc090d756fede8e5b716`. Do not redispatch or expand to full.
- [x] Retain the terminal run/step outcome and exact artifact inventory. If a quality
  report exists, verify payload integrity, evaluator SHA, dataset/corpus and twelve
  selected case identities before indexing it separately from full/repeatability evidence.
- [x] Recheck workload readiness and document limitations.
- [x] Validate the evidence update.
- [x] Obtain the owner's commit confirmation for this execution record.

Preparation observations on 2026-09-08 (Asia/Shanghai): local public readiness probing
had one connection reset; the runner-host public request returned HTTP 200 with all
dependencies up. An administrative token check initially assumed snake_case wire fields
and stopped before updating the Secret. Reusing the API's camelCase-aware response model
passed authentication; Secret metadata records `2026-09-07T16:29:24Z`. No token file,
model route change, application rollout or membership creation was needed.

Terminal result: checkout failed with Git exit 128 after TLS termination and GitHub
443 connection errors. Toolchain validation, dependency sync and evaluation were all
skipped; zero cases were submitted and the artifact inventory is empty. The runner
returned idle and all five Deployments stayed 1/1 Ready. A separately indexed failure
record preserves this outcome; no redispatch or model-quality conclusion was made.
Validation: the new checkout-failure contract first failed for the missing indexed
record, then passed. All 37 focused tests and 1013 non-integration tests passed (125
deselected), along with Ruff format/lint, mypy for 161 source files, Trellis context
validation and `git diff --check`. No integration or browser run was needed for this
evidence-only update; no temporary files were created.

## Slice 11: Reliable Hosted RAG Evaluation

- [x] Review the current repository, latest failure records, release scope and live
  readiness; write the local plan in `docs/ops/NEXT_STAGE_PLAN.md` and establish
  the current session goal. The owner requested planning and implementation.
- [x] Add a red workflow regression for default credential-free validation and an
  explicit live job gated by successful validation; move to hosted jobs and make it green.
- [x] Preserve frozen dependencies, 12/40 selection, bounded setup and execution,
  step-scoped token/allowlists, staging concurrency and exact run/attempt artifacts.
- [x] Execute the real offline CLI commands, verifying validation suite, selections,
  payload integrity and zero staging/model calls; inspect clean/shallow checkout behavior.
- [x] Update operating instructions and the executable workflow spec.
- [x] Run focused checks, non-integration pytest, Ruff, mypy, Actionlint, Trellis and diff checks.
- [x] Present the concrete commit/publication/single-trial action list for confirmation.
- [x] Obtain owner confirmation for the named commit, publication and single hosted trial.
- [x] Publish `a0d7439` and verify both Quality jobs in run `34158239990`.
- [x] Run hosted validate-only `34158544296`; retain its successful steps and reject
  both original sealed reports because their provenance records a dirty checkout.
- [x] Reproduce historical evidence clean-filter differences with an isolated Git index;
  add red-to-green byte/identity regressions and preserve evidence JSON/log bytes with `-text`.
- [x] Add a red-to-green workflow contract requiring a clean checkout after frozen sync
  and before every evaluator in both jobs, without application credentials.
- [x] Publish `a790aa4`, verify 1261 clean checkout files and Quality `34163699575`,
  and accept both clean hosted reports from preflight `34163826666`.
- [x] Verify the existing synthetic smoke owner, application readiness and release images;
  update the eight-hour token through memory/stdin and dispatch one twelve-case trial `34166173023`.
- [x] Retain trial `34166173023` attempt 1: all twelve cases completed and the exact
  sealed report passed source/integrity verification, with quality status `failed`.
  No redispatch occurred; the one-trial authorization has been consumed.

Local validation on 2026-09-08: three workflow behavior regressions failed for the
missing mode, self-hosted runtime and missing validation upload, then passed. The
combined workflow/evaluator suite passed 23 tests; full non-integration validation
passed 1017 tests (125 deselected). Ruff format/lint, mypy (161 source files),
Actionlint 1.7.7, Trellis and diff checks passed. Eight PowerShell examples parsed,
and the documented verifier passed eleven acceptance/rejection checks using synthetic
fixtures that were removed afterward. Both actual offline selections retained the
canonical dataset, corpus and lockfile hashes. Their provenance records local HEAD
`10aaebf` with dirty state; it is not hosted clean-checkout or real-provider evidence.
Source inspection confirms only HEAD/status Git queries, so shallow checkout is
sufficient. The implementation was subsequently published as `a0d7439`; its first
hosted preflight succeeded at dataset validation but failed operator provenance acceptance.
Preflight `34163826666` was subsequently accepted with both reports clean at `a790aa4`.
The authorized live trial then completed with a verified failed quality report.

The fresh-checkout diagnostic reproduced 84 dirty historical evidence paths under
`core.autocrlf=false` / `core.eol=lf` without changing the canonical index. Git clean
filters conflicted with stored CRLF/mixed blobs. Preserve original bytes with `-text`,
retain both original sealed reports and their rejected execution record, and require a
clean checkout before evaluation. Focused verification passed seven tests (the workflow
guard plus the six evidence contracts). This does not establish repaired hosted provenance.
Full non-integration regression passed 1021 tests (125 deselected); Ruff lint, mypy
(161 source files), Actionlint 1.7.7, Trellis and diff checks passed. Ruff formatting
required one assertion wrap, subsequently corrected without a behavior change.

Hosted acceptance on 2026-09-08: the full local checkout had 1261 clean files, the shell
guard accepted a clean fixture and rejected a controlled dirty fixture, and both Quality
jobs passed at `a790aa4`. The new hosted preflight passed. Live run `34166173023` completed
at 22:32Z with 7 succeeded, 2 expected refused and 3 failed Agent cases; automated scoring
passed 9/12. Fact/citation metrics were 0.70 against 0.90/0.95 targets. The three failed
cases expose only `agent_execution_failed` and null detailed diagnostics. Timeout fallback
was observed on three different successful cases. Usage was observed for 7/12; aggregate
tokens, billing and immutable model revisions remain unavailable. Post-run readiness was
healthy with unchanged v0.1.33 images and all synthetic-window jobs terminal.

This completes Slice 11's hosted execution-path objective, not the full M5/M7 quality
gate. `docs/ops/NEXT_STAGE_PLAN.md` prioritizes failure diagnosis and telemetry coverage
before newly authorized full-suite/repeatability work. Keep the M5 task in progress.
Final execution-evidence checks passed 36 focused workflow/evaluator/evidence tests,
five new raw-report byte/payload checks, terminal classifications, historical index
preservation, redaction and document links, Trellis context and `git diff --check`.
No application or evaluator code changed after the 1021-test `a790aa4` verification.

Rollback point: restore the reviewed previous workflow; keep the server runtime,
wheelhouse, existing staging release and all historical evidence. Full-suite repetition,
provider revision/cost, independent semantic review and M6-R5 retain separate acceptance.

## Slice 12: Unexpected Failure Diagnostics

- [x] Match all twelve trial cases by query SHA, synthetic owner and the exact execution
  window using read-only transactions. Inspect attempts, events and checkpoint presence.
- [x] Check bounded worker/consumer logs, deployed source hashes and the session-pool
  connection contract. Preserve the historical root cause as unresolved.
- [x] Reproduce Celery replacing the application JSON formatter in a local startup probe.
- [x] Add a red regression through real Celery logging setup; preserve structured,
  redacted application errors and make the regression green without a network connection.
- [x] Add a red Worker boundary regression for trusted unexpected-exception categories;
  add the Core allowlist and Worker classification while preserving cancellation, known
  typed errors, public codes and retryability.
- [x] Prove the new code survives durable attempt/status and sealed evaluator boundaries;
  continue rejecting arbitrary diagnostics and exception bodies.
- [x] Record exact read-only observations and limits in separate diagnostic evidence;
  update the index, executable specs and next-stage plan without altering old reports.
- [x] Run focused tests, necessary local integration checks, full non-integration checks,
  Ruff format/lint, mypy, Trellis and diff checks.
- [x] Present one concrete commit/publication/diagnostic rollout and bounded trial plan
  for the remaining owner review. Do not infer a new paid evaluation authorization.
- [x] Obtain owner confirmation for the exact sixteen-file commit, its subsequent
  fast-forward push to `origin/main` and Quality CI verification.

Local verification on 2026-09-08: 1050 non-integration tests passed (126 deselected),
and 41 related Job/Agent integration tests passed (16 deselected) on the existing local
Compose stack. Ruff lint/format passed for 378 Python files; mypy passed for 161 source
files. The integration regression uses the real graph, durable backend, consumer,
PostgreSQL and authenticated HTTP routes, injecting only initial checkpoint I/O failure.
It verifies one permanent failure, duplicate-delivery fencing, safe diagnostics, null
unobserved usage and tenant isolation. A separate process with the new allowlist disabled
failed both the durable and evaluator regressions at the expected null-diagnostic checks;
no source files were changed by this negative control.

The seven original sanitized tool observations were recovered from the session's actual
tool results and archived separately from the sealed quality report. An additional
retained-node-log scan at 2026-09-08T01:12Z correctly converts the CRI +08:00 timestamps
to UTC; the earlier lexical-filter node counts are invalid and explicitly superseded.
Both Pods have zero restarts. No underlying exception was found in the retained trial
window; external database/platform logs remain uninspected. The historical cause is
still unknown, so root-cause completion is not claimed. The next-stage plan contains the
exact sixteen-file commit batch and subsequent image/deployment/trial review boundaries.
Final archive checks verified all seven observation checksums, ten deployed/baseline/tested
source fingerprints, the unchanged original quality bytes, additive-only index updates,
sanitized fields, local document links and the exact sixteen-file dirty-state inventory.
The six evidence-contract tests, Trellis context validation and `git diff --check` passed.
The owner approved the exact sixteen-file commit, its subsequent push and Quality CI
verification on 2026-09-08. Tag/image publication, application deployment and any new real
trial remain outside this approval; no application deployment or real trial ran.

Public interfaces: Celery logging startup, `AgentExecutionHandler`, authenticated attempt
history and `run_staging_rag_quality`. Mock only process/DB/model boundaries; use actual
framework logging setup. Add one failing behavior and its implementation at a time.
No deployed configuration, model route, threshold, retry policy or historical data changes.

## Slice 12 Candidate Publication And Deployment Review

- [x] Publish the approved diagnostic commit `b0dde98` and verify both Quality jobs in
  `34185778632` attempt 1.
- [x] Build the subsequently authorized `v0.1.34` candidate at that exact commit;
  Container Supply Chain `34187312679` attempt 1 passed all four image jobs and manifest aggregation.
- [x] Verify the original manifest, all 56 evidence file hashes, exact signature
  identity/commit/digest bindings, signed SBOM/provenance and readable linux/amd64 OCI indexes.
- [x] Match the live namespace identity and v0.1.33 images, validate all 18 existing
  prerequisite objects and simulate the candidate change. Only four image approval
  annotations and the prerequisite fingerprint differ; ConfigMap content is identical.
- [x] Server-dry-run the guarded Namespace patch and prove no resourceVersion or
  annotation was persisted. Verify public readiness with the real smoke request headers,
  API Pod readiness and a zero-selection read-only reindex plan without provider calls.
- [x] Record the exact six-file evidence commit and subsequent staging deployment/smoke
  scope in `docs/ops/v0.1.34-staging-deployment-plan.md` for owner review.
- [x] Obtain that concrete publication/deployment confirmation before applying the
  Namespace patch, updating four rollback variables and three short-lived smoke secrets,
  or dispatching the single staging deployment with its paid-provider smoke boundaries.

The owner approved the exact six-file commit, push, CI verification and single deployment
scope at 2026-09-08T10:47:38Z in session `01a07cf9-63aa-70b0-9e02-0aefc8433e45`.
That turn failed before execution; the resumed session must refresh live preconditions before
changing the reviewed Namespace annotations, rollback variables or short-lived tokens.
The approval includes the documented provider smoke calls, not a new real RAG trial or rerun.
The candidate is prepared, not deployed. The default urllib readiness probe returned
403 while curl, the existing smoke User-Agent and Pod loopback returned 200; all actual
readiness checks reported three dependencies up. The failed default probe is retained.
CI performed cryptographic verification; local checks inspected its original results.
The optional local Cosign binary download was stopped without executing the partial file.
Historical root causes, M5/M7 quality and any new real RAG trial remain outside this result.

## Full Completion Gate

```powershell
uv sync --frozen
pnpm quality
uv run pytest -m integration -q
pnpm --filter web exec playwright test
uv run python scripts/evaluate_m3_retrieval.py
uv run python scripts/evaluate_m4_agent.py
uv run python scripts/evaluate_m5.py
uv run python scripts/load_m5.py --help
uv run python .trellis/scripts/task.py validate 07-19-m5-observability-eval-load
git diff --check
```

## Completion Rules

- A target is not a measured result.
- A local workstation result is not production capacity.
- Deterministic providers prove contracts and policy, not real-model answer quality.
- `blocked_external` requires a stable manual-gate record and does not satisfy a passed
  parent requirement.
- No M5 task is archived from uncommitted working-tree evidence alone.
