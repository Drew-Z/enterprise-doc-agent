# Observability, Evaluation And Load

## Adopted Facts

- `MetricsRuntime` owns one explicit Prometheus `CollectorRegistry` per process.
- API labels are bounded to method, route template and status class.
- Worker labels are bounded to known job type, claim/outcome, heartbeat and publish result.
- Readiness and model gateway wrappers emit bounded dependency duration/up metrics;
  database-pool, queue-age and Redis-connection gauges are explicit measured inputs and
  are never populated with invented defaults.
- Request, tenant, run, job, event, filename, object key, prompt and error text are not
  Prometheus labels.
- Fault injection is disabled by default, allowed only in local/test and selected only
  at Worker composition roots.
- `scripts/evaluate_m5.py` records dataset hashes, behavior versions, target and measured
  values; deterministic results are not real-provider quality.
- The staging RAG evaluator records the sorted target names represented by a selected sample and
  applies unchanged thresholds only to those metrics. A complete suite represents every answer
  and refusal metric, so all dataset targets remain mandatory; unavailable metrics are never
  invented or coerced to passing values.
- Public-reference-inspired RAG suites keep their fictional corpus, versioned dataset, and
  provenance record as one review unit. Static loader or `--validate-only` success preserves
  deterministic contracts only; it cannot create M5/M7 provider evidence and requires independent
  human content and semantic review before any provider trial.
- `scripts/load_m5.py` reports nearest-rank P50/P95/P99 and explicitly labels one bounded
  run as non-production capacity.
- `scripts/run_application_capacity.py` executes repeated ramp, steady-state, burst and
  recovery phases, preserves raw request samples and aggregate Prometheus snapshots, and
  cannot label a local-only run `passed`.
- The optional Compose profile includes an OTLP Collector with principal/request
  attribute deletion plus Prometheus recording and alert rules. Kubernetes leaves OTLP
  disabled until an actual collector endpoint is reviewed.

## Staging RAG Artifact Contract

### Scope / Trigger

The former self-hosted runner could retain older reports. Exact attempt isolation
remains mandatory on hosted jobs, and validation reports must never become quality evidence.

### Signatures

`evaluate-staging-rag-quality.yml` invokes `evaluate_staging_rag_quality.py --report-path`
with `$RUNNER_TEMP/enterprise-doc-rag-quality/rag-quality-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}.json`.
Validation instead writes two paths under `$RUNNER_TEMP/enterprise-doc-rag-validation/`:
`rag-validation-trial-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}.json` and
`rag-validation-full-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}.json`.

### Contracts

The artifact name is `staging-rag-quality-${{ github.run_id }}-${{ github.run_attempt }}`.
Upload `path` selects that exact report file and `if-no-files-found` is `error`.
`provenance.commit_sha` identifies the evaluator checkout; server image identity and
independent review must be recorded separately.

The validation artifact is `staging-rag-validation-${{ github.run_id }}-${{ github.run_attempt }}`
and lists only those two exact paths. Both jobs always attempt their own report upload;
neither uploads a whole directory. Validation reports use suite
`staging-real-provider-rag-quality-validation` and scope `local-dataset-validation`.
Live reports use suite `staging-real-provider-rag-quality` and scope
`authenticated-staging-real-provider-quality`. Operator verification must check the
expected suite/scope, clean evaluator SHA, canonical dataset/corpus hashes, selected
case IDs and payload integrity. A valid checksum is not a signature, quality pass or review.

### Validation & Error Matrix

- Current sealed report exists: upload it even if evaluation thresholds failed.
- Current report missing: fail the upload; retain the run as incomplete.
- Earlier reports remain: exclude them from the current attempt's artifact.
- Only validation reports exist: verify dataset preparation, never live quality.
- Full-selection validation fails after trial validation: retain any current validation
  output, keep the job failed and skip the dependent live job.

### Good/Base/Bad Cases

- Good: one matching run/attempt report, with intact payload hash and matching checkout SHA.
- Base: a sealed `status: failed` report is retained for diagnosis.
- Bad: a prior attempt's passing report is substituted after timeout or cancellation.

### Tests Required

`test_staging_rag_quality_upload_excludes_stale_runner_reports` fixes the exact file
selection, single-upload boundary, missing-file error and always-upload behavior.
`test_staging_rag_validation_artifacts_are_separate_and_run_scoped` fixes both validation
paths and the distinct artifact. Evaluator tests verify seals, exact 12/40 CLI selections,
absence of staging client creation during validation and credential/raw-output redaction.

### Wrong vs Correct

Do not upload `${{ runner.temp }}/enterprise-doc-rag-quality/` as a directory.
Use `${{ runner.temp }}/enterprise-doc-rag-quality/rag-quality-${{ github.run_id }}-${{ github.run_attempt }}.json`.

## Staging RAG Hosted Execution And Startup Contract

### Scope / Trigger

A cold dependency download exhausted trial `33976542098`; a prepared runtime then
could not prevent checkout failure in trial `34143634860`. Neither reached model
evaluation. The hosted successor separates credential-free dataset preparation from
explicit live execution while preserving bounded setup and the original failure records.

### Signatures

`execution_mode` is `validate-only|evaluate`, defaulting to `validate-only`.
Both jobs use `ubuntu-24.04`, the repository's pinned setup Actions, Python from
`.python-version` and uv `0.11.3` with a `uv.lock` cache key. The five-minute sync step
runs `uv sync --frozen --no-dev --python python`. Evaluator commands use
`uv run --no-sync python scripts/evaluate_staging_rag_quality.py`.

`validate` invokes `--validate-only` twice, with and without `--trial-only`, against
`evaluation/rag_quality_v2.json`. `evaluate` declares `needs: validate` and condition
`${{ inputs.execution_mode == 'evaluate' && needs.validate.result == 'success' }}`.

### Contracts

`validate` has a 15-minute job limit, no staging Environment and no application secrets.
Each hosted job gets a fresh VM and its own checkout/runtime. Checkout uses depth one,
default cleanup and `persist-credentials: false`; provenance only needs HEAD and dirty
state. Normal Quality CI retains full history for historical-evidence checks.

After frozen sync and before any evaluator, both jobs run a one-minute, credential-free
`Require clean evaluator checkout` step: `test -z "$(git status --porcelain)"`.
A dirty checkout must stop execution before validation or token-bearing model calls.
Report verification still independently requires `working_tree_dirty: false`.

Evidence JSON/log files use `.gitattributes` `-text`: their recorded hashes describe
original bytes, including historical CRLF/mixed blobs. Applying `text eol=lf` to those
blobs can make Git's clean-filter hash differ from the index even when checkout bytes
match the stored blob. Preserve existing bytes; do not renormalize historical evidence,
ignore dirty state, or edit sealed reports to make provenance appear clean.

Keep the live job's staging Environment, 40-minute ceiling, 1800-second evaluator window,
serial cases and explicit `trial|full` choice. Token, base URL and host allowlists are
step-scoped; setup receives no application credentials and evaluation cannot implicitly
sync. Do not add Kubernetes or SSH credentials. The shared staging concurrency group
serializes workflows, while operators separately avoid manual deployment/reindex/load
overlap. Environment ref restrictions do not establish required-reviewer approval.

Hosted publication and one authorized trial are distinct from local validation. A
dataset-only pass does not prove hosted access to the API/object store or real quality.
Retain the selected report even when thresholds fail; do not redispatch to select a pass.

### Validation & Error Matrix

- Default mode: validate both selections, publish validation artifacts and skip live execution.
- Live mode with successful validation: prepare a fresh runtime, then evaluate without sync.
- Validation or its setup/upload fails: skip the live job, with no application token use.
- Checkout fails: retain its Git error classification and skipped downstream steps;
  do not diagnose dependency or provider failure from a missing report alone.
- Setup fails or times out: skip evaluation and retain missing-report upload failure.
- Dirty checkout after setup: fail the clean-checkout step and skip evaluators; retain
  the provenance failure separately from quality. A successful workflow with an old
  dirty report is still rejected by operator verification.
- Evaluator never starts: record zero submitted cases and no quality report, not failed
  answer metrics or successful token authentication.

### Good/Base/Bad Cases

- Good: unchanged 12/40 selections validate without staging credentials or a network client.
- Good: a full validation artifact cannot be mistaken for a full live quality report.
- Base: incomplete dependency setup is indexed separately from full quality evidence.
- Bad: a longer model timeout, disabled dependency verification, or repeated dispatch
  is used to conceal the failed startup attempt.

### Tests Required

`test_staging_rag_quality_bounds_dependency_setup_before_token_use` checks the setup
limit, runtime-only frozen install, interpreter, ordering and no-sync execution.
`test_staging_rag_quality_validates_before_explicit_live_execution` checks the default,
secret-free validation job and explicit successful-predecessor condition.
`test_staging_rag_quality_jobs_use_isolated_pinned_hosted_runtimes` checks both runners,
fixed setup Actions, shallow cleanup, credential persistence and setup ordering.
`test_staging_rag_quality_rejects_dirty_checkouts_before_any_evaluator` checks the
credential-free guard in both jobs after sync and before every evaluator command.
`test_evidence_checkout_round_trip_preserves_recorded_bytes` uses real Git filters and
an isolated index for historical JSON/log blobs, requiring both byte equality and the
same clean-filter Git blob identity after checkout.
`test_staging_trial_setup_failure_does_not_replace_provider_quality` keeps the failed
attempt distinct from historical full-suite results and open external gates.
`test_staging_runtime_preparation_is_not_provider_quality_evidence` checks the separately
indexed preparation record, zero live calls and transferred wheel hashes against the
exact recorded lockfile commit, without replacing historical quality evidence.
`test_staging_trial_checkout_failure_is_not_runtime_or_model_failure` preserves the
checkout failure separately, with zero submitted cases, no report, and a distinction
between operator session authentication and a skipped workflow evaluation step.

### Wrong vs Correct

Do not use an unbounded `uv sync --frozen` followed by an implicitly syncing `uv run`
in the token-bearing step. Bound runtime-only setup first and use `uv run --no-sync`;
verify runner dependency readiness separately before a newly authorized trial.
Do not put the staging Environment or application secrets on the validation job, infer
quality from `status: passed` alone, or disable default checkout cleanup.

### Historical Runtime Recovery

Keep the previously prepared server runtime at
`/home/gha-staging/enterprise-doc-agent-evaluator-runtime/.venv` and its verified
wheelhouse. Hosted jobs do not use or remove them. Restoring the former workflow
requires revalidating its single job-level `UV_PROJECT_ENVIRONMENT`, executable Python
preflight and frozen sync before token use. The preserved preparation evidence covers
109 installed packages and offline rebuilds of four workspace packages; it does not
prove a complete original registry cache. Changes to lockfile, Python ABI, platform or
checkout path require renewed preparation. Check transferred wheels against original
lockfile hashes/sizes, and never edit uv cache internals or disable TLS verification.

## Proven Examples

- `apps/api/tests/test_metrics.py` and `packages/core/tests/test_metrics.py` verify
  process-local registries and bounded metric labels.
- `tests/evaluation/test_fault_drill.py` proves that fault drills are local/test-only,
  sanitize reports and refuse staging or production targets.
- `infra/observability/` provisions Prometheus scrape targets and Grafana dashboards for
  API RED, Worker, consumer, outbox, heartbeat and dependency signals, plus local OTLP
  receipt and alert-rule evaluation.
- `tests/deployment/test_run_application_capacity.py` proves the repeated matrix can
  produce a validator-accepted report only when dependency telemetry is present.
- `evidence/m5/20260719-redis-outage-recovery.json` and the MinIO companion report record
  guarded local readiness loss/recovery runs without claiming production failover.
- `evidence/m5/20260719-observability-stack-runtime.json` records successful local profile
  startup and dashboard provisioning while explicitly retaining down target status.
- `evidence/m5/20260719-m5-unified-evaluation.json`, the 100-request health baseline,
  and `evidence/m5/20260719-m5-local-ready-resource-load.json` are local evidence. The
  ready run uses 1000 requests at concurrency 20 with host and selected API-process
  resource sampling; neither run is representative production-quality or
  production-capacity evidence.
- `evaluation/rag_quality_public_reference_v1.json`, its `corpus/public_reference_inspired_v1/`,
  and `rag_quality_public_reference_v1.provenance.md` demonstrate a standalone synthetic suite
  with source-boundary review; they are not provider-quality evidence.

## Proven Files

- `packages/core/src/enterprise_doc_core/telemetry/metrics.py`
- `apps/api/src/enterprise_doc_api/middleware/metrics.py`
- `apps/worker/src/enterprise_doc_worker/faults.py`
- `packages/core/src/enterprise_doc_core/evaluation/contracts.py`
- `tests/evaluation/test_m5_load.py`
- `scripts/run_application_capacity.py`
- `infra/capacity/application-capacity.example.yaml`
- `infra/observability/otel-collector-config.yaml`
- `infra/observability/rules/enterprise-doc-agent.rules.yml`
