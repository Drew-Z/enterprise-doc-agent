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
