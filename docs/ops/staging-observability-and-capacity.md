# Staging Observability And Capacity

This document records the non-production observability and bounded load checks for
the single-node staging deployment. It does not promote the environment to a
production SLO or capacity claim.

## Readiness behavior

`GET /health/live` is a process-only liveness check and never contacts a dependency.
`GET /health/ready` checks PostgreSQL, Redis, and both object-store buckets. The API
uses `API__READINESS_CACHE_TTL_SECONDS` (2 seconds in the reviewed Kubernetes base
ConfigMap) to prevent readiness probes and load bursts from multiplying external
dependency checks:

- the first request performs the real checks;
- requests inside the TTL reuse the latest result;
- after expiry, one request refreshes while concurrent requests receive the latest
  bounded result;
- a failed refresh is cached as `not_ready`, so an outage is not hidden indefinitely;
- set the value to `0` for local tests that require an uncached check on every request.

Each readiness response also includes a server-generated UTC `checked_at` timestamp.
The Web runtime surface displays this probe time (rather than the browser fetch time)
so an operator can distinguish a fresh dependency observation from a cached response.
Older clients may ignore the field; it does not change the readiness decision.

This cache is an availability/readiness control, not a replacement for dependency
metrics or alerting. Kubernetes liveness remains independent.

## Internal metrics

Metrics are not exposed through the public Ingress. The API and Worker expose
Prometheus text on their internal Services, and the Consumer exposes the same
registry on its metrics port. For an operator-only inspection:

```bash
sudo kubectl -n enterprise-doc-agent-staging port-forward \
  svc/enterprise-doc-api 19000:8000
curl --fail http://127.0.0.1:19000/metrics
```

Use an equivalent temporary forward for `enterprise-doc-worker:8081` and
`enterprise-doc-consumer:8082`; terminate the forward after inspection. Do not add
the metrics paths to the public Ingress or copy raw metric output into an evidence
artifact if it contains environment-specific labels.

The bounded labels include API route templates/status classes, known job types,
dependency names, queue/publish results, and business boundary outcomes. Tenant,
document, run, user, object-key, prompt, and error text are not metric labels.

The `single-node-4c8g` profile also runs a digest-pinned Prometheus behind an internal
ClusterIP. It discovers the headless metrics Services every 30 seconds and scrapes every
API, Worker, and Consumer Pod every 15 seconds, so replicas are not hidden behind a
load-balanced ClusterIP. Each application registry includes Linux process CPU and
resident-memory collectors without business identifiers. A `local-path` PVC is bounded
to 5 GiB and Prometheus independently enforces both seven-day and 4 GB TSDB retention.
This preserves samples across Pod replacement, but not node or disk loss.
There is deliberately no public Ingress, NodePort, Grafana or Alertmanager.

Use an operator-only port forward to inspect the retained view:

```bash
kubectl -n enterprise-doc-agent-staging port-forward \
  svc/enterprise-doc-prometheus 19090:9090
curl --fail http://127.0.0.1:19090/-/ready
curl --fail --get http://127.0.0.1:19090/api/v1/query \
  --data-urlencode 'query=up{job=~"enterprise-doc-(api|worker|consumer)"}'
curl --fail http://127.0.0.1:19090/api/v1/targets
curl --fail http://127.0.0.1:19090/api/v1/rules
```

Every discovered Pod target must be present with `health: up`; recording and alert rules
must be loaded without evaluation errors. Stop the forward after inspection. Retain only
the bounded job/target counts, aggregate status, image digest and rule counts as evidence,
not a raw TSDB or unbounded metric dump.

To prove that Pod replacement keeps the PVC-backed history, record a restart epoch,
replace the Prometheus Pod, wait for rollout, and query a range spanning that epoch:

```bash
restart_epoch="$(date +%s)"
kubectl -n enterprise-doc-agent-staging delete pod \
  -l app.kubernetes.io/name=enterprise-doc-prometheus
kubectl -n enterprise-doc-agent-staging rollout status \
  deployment/enterprise-doc-prometheus --timeout=300s
kubectl -n enterprise-doc-agent-staging get pvc enterprise-doc-prometheus-data
curl --fail --get http://127.0.0.1:19090/api/v1/query_range \
  --data-urlencode 'query=up{job="enterprise-doc-api"}' \
  --data-urlencode "start=$((restart_epoch - 120))" \
  --data-urlencode "end=$((restart_epoch + 120))" \
  --data-urlencode 'step=15'
```

The same result series must contain successful samples from before and after the restart.
A Bound PVC alone does not prove history was reopened.

## Retained-observability deployment observation

[Deploy Staging run 30970431550](https://github.com/Drew-Z/enterprise-doc-agent/actions/runs/30970431550)
completed successfully at commit `2b33f7caf4fb54ba69b3cb03b2a973ae6adeebcd`.
The workflow rolled out Prometheus with the release; a separate operator-only drill then
verified the retained state rather than treating workflow success as storage evidence:

- `enterprise-doc-prometheus-data` was `Bound` at its requested `5Gi` capacity;
- API, Worker and Consumer targets were all present and `up`;
- all nine loaded recording and alert rules were healthy, with no evaluation errors;
- `/api/v1/series` inventories using one exact job matcher at a time returned 238 API,
  97 Worker and 161 Consumer series at the observation time;
- Prometheus Pod replacement completed while reusing the same PVC; and
- the API `up` range query contained eight successful pre-replacement samples and seven
  successful post-replacement samples.

These counts are point-in-time staging observations, not cardinality budgets. The drill
proves that the current Prometheus Pod reopened PVC-backed history after Pod replacement.
It does not prove recovery from node or disk loss, high availability, managed retention,
alert delivery or production capacity.

## Alert response

- `EnterpriseDocMetricsTargetDown` means Prometheus discovered a target but cannot scrape
  it. Check the target error, Service endpoints, Pod readiness and the metric-only
  NetworkPolicies before restarting anything.
- `EnterpriseDocMetricsTargetAbsent` means an expected scrape job was not loaded. Check
  the mounted ConfigMap, Prometheus startup log and `/api/v1/status/config`.
- API, Worker, outbox or dependency alerts require the corresponding application logs,
  queue state and dependency health to be correlated. Do not infer a root cause from one
  ratio.
- The ratio recording rules use an epsilon denominator so low staging traffic retains
  its real error ratio; a denominator of one request per second would hide failures.
- There is no Alertmanager in this profile. Rules are evaluated and inspectable, but no
  page or ticket is delivered until a separately reviewed receiver is connected.

## Bounded staging load

The following command exercises only the public readiness endpoint and is useful for
detecting regressions in the control-plane path:

```powershell
uv run python scripts/load_m5.py `
  --scenario ready `
  --base-url https://agent.playlab.eu.cc `
  --requests 200 `
  --concurrency 20 `
  --request-timeout-seconds 15 `
  --report-path C:\path\outside\repo\staging-ready-load.json
```

The report must remain labelled as a bounded run unless it includes repeated ramp,
steady-state, burst, and recovery phases, immutable image identity, dependency
telemetry, and an isolated production-like environment. A non-zero error rate or a
latency regression is a diagnostic signal, not evidence to suppress by raising the
target.

## Protected staging RAG quality execution

`Evaluate Staging RAG Quality` is a manual GitHub Actions workflow for the reviewed
`evaluation/rag_quality_v2.json` corpus. The hosted implementation uses a fresh
`ubuntu-24.04` VM for each job and shares the `enterprise-doc-agent-staging` concurrency
lock with deployment and rollback. Publication and live verification are tracked in
[the next-stage plan](NEXT_STAGE_PLAN.md); local implementation does not establish that
the remote workflow has changed or that hosted-to-staging networking works.

`execution_mode` defaults to `validate-only`. Its `validate` job has no staging
Environment or application credentials, and checks both the twelve-case trial and the
full forty-case selection without staging, embedding or model calls. Only explicit
`execution_mode=evaluate` can start the live job, and only after `validate` succeeds.
The live job alone references the `staging` Environment; its evaluation step alone
receives the short-lived `STAGING_SMOKE_TOKEN` and both host allowlists. It calls the
public HTTPS control plane and object store without Kubernetes or SSH credentials.
The Environment name alone does not configure deployment protections or independent review.

`evaluation_scope` applies to live execution and defaults to `trial`, selecting the
twelve explicitly marked v2 cases. Choose `full` only after the provider route, revision,
provider billing inputs, approved corpus scope and human reviewer are available.
Validation artifacts are named `staging-rag-validation-<run-id>-<attempt>` and contain
only the exact trial/full validation JSON files. Live artifacts retain the separate
`staging-rag-quality-<run-id>-<attempt>` name and exact quality JSON file. Never use a
validation report as evidence of model quality.

Both jobs use pinned setup Actions, Python from `.python-version`, uv `0.11.3` and a
lockfile-keyed dependency cache. Each frozen runtime-only sync has its own five-minute
limit: `uv sync --frozen --no-dev --python python`. Every evaluator invocation uses
`uv run --no-sync`. Validation has a 15-minute job limit; live evaluation retains the
40-minute job limit and 1800-second evaluator window. Checkout fetches one commit,
keeps default cleanup enabled and does not persist Git credentials. Provenance reads
HEAD and dirty state only, so this evaluator does not require full Git history.

After sync, both jobs require a clean `git status --porcelain` before invoking any
evaluator. The one-minute guard receives no application credentials. Historical
evidence JSON/log files use `-text` attributes to retain their original bytes; do not
renormalize those files or suppress dirty provenance to get a passing run.

The previously prepared server environment and verified wheelhouse are retained as
[recovery material](#prepared-linux-runtime-on-2026-09-07). Hosted jobs do not use or
modify that environment. Setup success does not prove public API or object-store
reachability; those boundaries still require an actual authorized trial.

A passing validation workflow proves dataset/runtime preparation only. A passing live
workflow proves the selected evaluation met its recorded thresholds on the observed
route. Neither closes M5/M7 by itself: stable provider revision and cost metadata,
representative-corpus review and independent human semantic approval remain required.
Do not substitute the public-reference-inspired synthetic suite in this workflow.
Quality reports retain hashed queries/answers, route/behavior identities, token telemetry
and diagnostics while excluding bearer tokens, raw bodies and signed URLs.

### Preconditions and configuration

Use the [4C4G runbook](single-node-4c4g-staging-runbook.md) for the current host.
The shared provisioning procedures in the tiny runbook remain useful, but its historical
configured/missing inventory is not a current Environment audit. Check the following
before each live evaluation window. Credential-free validation does not need the
Environment, token or live-service rows:

| Item | Required value or observation |
| --- | --- |
| Workflow revision | The workflow is published on the remote default branch; the selected branch/tag contains the reviewed evaluator and frozen dependencies. Local commits cannot be dispatched. |
| Environment | Existing `staging` Environment with its allowed refs and review protection checked; dispatch only a reviewed ref. |
| Runner | GitHub-hosted `ubuntu-24.04` jobs available; no overlapping deployment, rollback or manual load/reindex work. |
| Toolchain and setup network | Pinned Python/uv setup succeeds; GitHub, PyPI and the original lockfile download URLs are reachable within the bounded setup window. |
| Hosted validation | Successful validate-only run for the reviewed SHA, with both 12/40 validation reports verified. Each live dispatch also repeats this prerequisite job. |
| Live network | Public HTTPS API and exact object-store hostname accessible from the hosted VM; dataset-only validation does not establish this. |
| `STAGING_ALLOWED_HOST` | Environment variable `agent.playlab.eu.cc`, a hostname without a scheme or path. |
| `STAGING_OBJECT_STORE_ALLOWED_HOST` | Environment variable containing the exact presigned-upload object-store hostname, without a scheme or path. |
| `STAGING_SMOKE_TOKEN` | Environment secret for the dedicated active synthetic smoke tenant/user; valid through approval/queue delay and the evaluation window. |
| Staging release | Latest accepted release record and immutable image identities retained; readiness and authenticated business smoke healthy. |

Rotate the token through the existing administrator-operated
[smoke token procedure](tiny-staging-runbook.md#first-rollout), immediately
before the window. The evaluator step alone receives that secret. It does not mint
tokens, provision membership, change model routes, or reindex existing documents.

In `execution_mode=evaluate`, both `trial` and `full` upload the selected synthetic documents, run ingestion/embedding,
and create Agent runs and answer artifacts in that tenant. Cases run sequentially;
the evaluator uses a shared 1800-second deadline and the job has a 40-minute ceiling.
Provider calls can incur charges. Cancellation or timeout does not undo uploaded data
or cancel already submitted server jobs; the evaluator has no automatic tenant cleanup.
Retain failed-attempt evidence and review that tenant's data before any operator cleanup.

The workflow serializes only with workflows using its concurrency group. Avoid concurrent
manual deployment, reindex or load runs. Provider revision, billing inputs, corpus approval
and semantic reviewer are operator prerequisites, not automatically enforced dispatch
inputs. Review the v2 synthetic corpus before the trial; a full v2 run still does not
establish representative enterprise-corpus quality.

### Read-only GitHub observation on 2026-09-05

The repository API returned the following state before publishing this workflow:

- The repository is public. The `staging` Environment exists, but `protection_rules`
  is empty and `deployment_branch_policy` is `null`. Environment protection is therefore
  a pending configuration requirement, not a verified current capability.
- `main` has neither branch protection nor repository rulesets. The repository's default
  workflow token permission is `write`; all eight local workflows already declare their
  own explicit permissions, including explicit package/signing grants for the image build.
- The 4C4G staging runner is online and idle. The legacy tiny runner is offline; no runner
  was deregistered. Labels identify a runner but do not isolate untrusted code from its host.
- All 23 Environment variables were enumerated with pagination. The deployment profile
  is `single-node-4c4g`; both required host allowlists exist and match the configured
  application and object-store hosts.
- `STAGING_SMOKE_TOKEN` exists and was last updated at `2026-09-03T16:09:27Z`. Secret
  metadata cannot establish JWT expiry or active membership; neither was validated here.
- The remote workflow inventory does not yet include `Evaluate Staging RAG Quality`.
  Remote `main` is `9e9efb52a27a7a7ccf963e68d97c95722cbb72f5`, also used by the successful
  `v0.1.33` deployment run `33777258980`. Local evaluator/docs commits are not published.

Proposed publication scope, pending owner authorization:

1. Change the repository default workflow token permission to `read`, preserving the
   existing explicit workflow grants and disabled Actions approval of pull requests.
2. Restrict the `staging` Environment to branch `main` and release tags `v*.*.*` using
   custom deployment branch/tag policies. Preserve visibility, secrets and runner
   registrations. This would exclude the earlier feature-branch deployment path.
3. Keep reviewer policy explicit: ref restrictions do not add independent approval,
   protect branch contents, or isolate the node-hosted runner. Reviewer ownership and
   independent semantic review remain separate decisions; do not claim they are enabled.
4. Publish the reviewed local commits as a fast-forward to `main`, verify the resulting
   workflow and CI, then refresh the dedicated smoke token through the existing procedure
   and execute one recorded 12-case trial. Full evaluation retains the prerequisites above.

These observations are a dated snapshot, not a quality report. All GitHub calls in this
check were read-only; no protection settings, tokens, server workloads or remote refs
were changed. Re-read the live settings and remote SHA before applying the proposed scope.

### Applied publication and failed trial on 2026-09-06

After owner approval, the repository default workflow permission was changed to `read`,
with Actions PR approval still disabled. The `staging` Environment now allows only branch
`main` and tags matching `v*.*.*`. API read-back confirmed the custom ref policy; no required
reviewer was added and administrator bypass remains enabled. These changes do not protect
branch contents or isolate the node-hosted runner.

The 14 reviewed local commits were fast-forward published through evaluator commit
`5bd1e6d830bc0c1737b33f899b453fd93143e0b8`. Both jobs in
[Quality run 33976238888](https://github.com/Drew-Z/enterprise-doc-agent/actions/runs/33976238888)
passed. The dedicated active smoke owner's short-lived token was rotated in memory and
the Environment secret updated at `2026-09-05T15:59:41Z`, with no token file or log output.

The single authorized
[trial run 33976542098, attempt 1](https://github.com/Drew-Z/enterprise-doc-agent/actions/runs/33976542098)
then exhausted its 40-minute job deadline. Frozen dependency sync ran from
`2026-09-05T16:04:38Z` to `16:41:21Z`; the evaluator was skipped, zero cases were submitted,
and the exact-report upload failed because no report existed. The artifact inventory is
empty. This is a setup failure, not twelve failed answers, a token-authentication result,
or evidence that the current models are unusable.

The lockfile uses PyPI and `files.pythonhosted.org`; logs and process observations show
ongoing ordinary Python package downloads, including development tools such as Ruff and
mypy. A registry probe returned HTTP 200. Post-run memory availability was 1625 MiB and
the kernel log contained no OOM match during the job window. These observations establish
incomplete dependency setup, not the precise cause of low download throughput. All five
application Deployments remained 1/1 Ready, the runner returned online/idle, and the API
image still matched the accepted `v0.1.33` release. No application rollout, model change,
swap change, or second trial was performed.

The sanitized [execution failure record](../../evidence/m5/20260906-staging-rag-trial-33976542098-setup-failure.json)
is indexed separately from historical full-suite and repeatability results. The subsequent
startup hardening above passed workflow contract tests and local offline runtime-only
validation of both 12/40 selections with unchanged dataset/corpus hashes. Those local
checks do not prove Linux runner download speed or real-provider quality.

Before another separately recorded trial, complete runtime-only dependency preparation
on the runner and verify the unchanged lockfile, evaluator imports and both offline
selections. Diagnose the package download path separately; do not silently switch package
indexes, disable TLS/hash checks, lengthen the model budget, or use an old report. Refresh
the dedicated token only after preparation is ready. The original failed run remains
failed, and no full-suite gate is closed by this publication or its local checks.

### Prepared Linux runtime on 2026-09-07

The [preparation record](../../evidence/m5/20260907-staging-rag-evaluator-runtime-preparation.json)
records 109 installed Linux runtime packages, without Ruff or mypy. Two bounded direct
downloads did not finish installation. Five missing wheels (35,343,009 bytes) were then
downloaded locally from the original lockfile URLs, checked for exact SHA-256 and size,
transferred over SSH, and checked again on the runner. An offline `uv pip install
--no-index --find-links <wheelhouse> --no-deps` seeded those exact five versions; the
unchanged `uv sync --frozen --no-dev --offline` installed the remaining 104 packages.
No lockfile, package index, TLS setting or internal cache file was changed.

Frozen sync with `--find-links` alone had still requested missing original registry URLs.
A later dry-run against a nonexistent environment also required those five distributions.
The installed environment is ready, but the original registry cache is not independently
complete. Retain both the environment and the verified wheelhouse; do not delete the
environment to test this claim. Recovering a missing environment requires reviewed
preparation, including rechecking the original lockfile hashes before any offline seed.

The runtime parent and `wheelhouse` are owned by `gha-staging`, mode `0700`. Use that
account, not a global Git `safe.directory` exception. In an operator Tailnet SSH session,
with the runner idle and no concurrent manual deployment or preparation, run this Linux
check without a token:

```bash
sudo -n -u gha-staging -H sh -s <<'RUNNER'
set -eu
cd /opt/actions-runner/_work/enterprise-doc-agent/enterprise-doc-agent
export UV_PROJECT_ENVIRONMENT=/home/gha-staging/enterprise-doc-agent-evaluator-runtime/.venv
RUNNER_UV=/opt/enterprise-doc-toolchain/python/bin/uv
RUNNER_PYTHON=/opt/enterprise-doc-toolchain/python/bin/python
test -x "$UV_PROJECT_ENVIRONMENT/bin/python"
timeout --foreground --signal=TERM --kill-after=10s 60s \
  "$RUNNER_UV" sync --frozen --no-dev --python "$RUNNER_PYTHON" --offline --check
"$RUNNER_UV" run --no-sync --offline python scripts/evaluate_staging_rag_quality.py \
  --dataset evaluation/rag_quality_v2.json --validate-only --trial-only
"$RUNNER_UV" run --no-sync --offline python scripts/evaluate_staging_rag_quality.py \
  --dataset evaluation/rag_quality_v2.json --validate-only
RUNNER
```

The existing checkout at `5bd1e6d830bc0c1737b33f899b453fd93143e0b8` passed this offline
check and explicit rebuilds of all four local workspace packages. Both selections passed
with unchanged dataset/corpus hashes and valid payload checksums. Scoped Git comparisons
confirmed those runtime inputs match `a1255e809c980cb9c3eaeed35df31b93313bab8d`; unrelated
pre-existing evidence/log differences in the runner checkout were left intact.

At recording time the workflow environment change is local and unpublished. A new Actions
checkout and real evaluation have not run with it. Changes to the lockfile, Python ABI,
platform or checkout path require renewed preparation. Offline validation does not prove
token authentication, answer quality, repeatability or production capacity. Publish the
reviewed change and verify CI, then obtain authorization for a separately recorded trial
and refresh the short-lived token immediately before that window.

### Prepared-runtime trial checkout failure on 2026-09-08

Commit `c1e2ec7a6bb80da8fc68dc090d756fede8e5b716` was published after approval;
both jobs in [Quality run 34142156900](https://github.com/Drew-Z/enterprise-doc-agent/actions/runs/34142156900)
passed. The owner then authorized one new twelve-case trial. Preflight confirmed the
109-package runtime and unchanged trial selection, active dedicated smoke ownership,
public readiness from the runner host, and all five application Deployments at 1/1.
The refreshed eight-hour token passed a separate loopback `/api/session` check before
the Environment secret was updated at `2026-09-07T16:29:24Z`. Administrative checks must
parse the camelCase wire response through `SessionResponse`, not assume snake_case keys.

[Trial run 34143634860, attempt 1](https://github.com/Drew-Z/enterprise-doc-agent/actions/runs/34143634860)
failed after 8m20s at checkout, before toolchain validation, dependency sync or model
evaluation. Git reported GnuTLS receive error `-110`, connection failures to GitHub port
443 and exit code 128. The exact-report upload failed because no report existed; the
artifact API returned zero artifacts. Zero cases were submitted, and this attempt did
not exercise embedding, chat or the prepared dependency-sync step. This is not another
dependency-install timeout or a model-quality result.

The [checkout failure record](../../evidence/m5/20260908-staging-rag-trial-34143634860-checkout-failure.json)
retains the run, source selection, diagnostics and preflight separately from old results.
Afterward the runner was online/idle, all five Deployments remained Ready, and the
original checkout and persistent Python executable still existed. A later repository
webpage HEAD request returned 200; that does not prove the earlier Git protocol transfer
was healthy or identify the underlying network cause.

Do not redispatch unchanged or increase model budgets. First diagnose the source-fetch
path or review a different evaluator execution location. Moving the job to a hosted
runner also moves the short-lived credential's execution boundary and needs review;
it is not part of this failed attempt. No runner migration, application rollout, model
change, proxy/DNS change or second trial was performed. M5/M7 gates remain open.

### Validate and dispatch from PowerShell

From the repository root with dependencies already installed, these commands validate
both fixed selections without a token or staging, embedding or chat calls. Expect
12 and 40 selected cases:

```powershell
uv run --no-sync python scripts/evaluate_staging_rag_quality.py `
  --dataset evaluation/rag_quality_v2.json --validate-only --trial-only
if ($LASTEXITCODE -ne 0) { throw 'Trial dataset validation failed' }
uv run --no-sync python scripts/evaluate_staging_rag_quality.py `
  --dataset evaluation/rag_quality_v2.json --validate-only
if ($LASTEXITCODE -ne 0) { throw 'Full dataset validation failed' }
```

After publishing the reviewed workflow and verifying Quality CI for that exact SHA,
dispatch the hosted dataset-only preflight:

```powershell
$ragRepo = 'Drew-Z/enterprise-doc-agent'
gh workflow view evaluate-staging-rag-quality.yml --repo $ragRepo --ref main --yaml
if ($LASTEXITCODE -ne 0) { throw 'Reviewed remote workflow is not available' }
gh workflow run evaluate-staging-rag-quality.yml --repo $ragRepo --ref main `
  -f execution_mode=validate-only
if ($LASTEXITCODE -ne 0) { throw 'Validation dispatch failed' }
```

Retain the returned run URL/ID and verify its commit in Actions. If the CLI does not
return a URL, locate the exact dispatch by workflow, actor, ref and start time; do not
assume the newest repository run is yours. Verify both validation artifacts below.
After approval for the hosted execution location and a single trial, recheck the live
prerequisites, refresh the dedicated token and dispatch once:

```powershell
gh workflow run evaluate-staging-rag-quality.yml --repo $ragRepo --ref main `
  -f execution_mode=evaluate -f evaluation_scope=trial `
  -f staging_base_url=https://agent.playlab.eu.cc
if ($LASTEXITCODE -ne 0) { throw 'Trial dispatch failed' }
```

This live dispatch repeats the prerequisite validation job. After reviewing the trial
and full-run prerequisites, a separately approved full dispatch also requires
`-f execution_mode=evaluate` with `-f evaluation_scope=full`. Omitting the mode performs
only validation. Preserve every attempt; a later pass does not erase a failed attempt.

### Retrieve and verify validation or quality reports

The first hosted preflight, run `34158544296` at `a0d7439`, completed successfully on
GitHub, but both sealed 12/40 reports recorded `working_tree_dirty: true` and were
rejected by the verifier. A local fresh checkout with Linux Git settings reproduced
84 dirty historical evidence paths caused by newline clean filters. The
[provenance failure record](../../evidence/m5/20260908-staging-rag-validation-34158544296-provenance-failure.json)
preserves the original reports and distinguishes that operator rejection from the
successful dataset-only steps. No model calls ran. The byte-preservation and clean-checkout
guard fix was published as `a790aa4`; new preflight `34163826666` passed both clean reports,
recorded in the [accepted preflight](../../evidence/m5/20260908-staging-rag-validation-34163826666-execution.json).

The subsequent authorized trial `34166173023`, attempt 1, completed all twelve cases
on that evaluator SHA and uploaded its exact sealed report. Its quality result is failed:
7 Agent runs succeeded, 2 refused as expected, and 3 failed with `agent_execution_failed`.
Fact/citation metrics were 0.70; the required thresholds remain 0.90/0.95. Detailed
diagnostics for those failures are null. Three separate successful cases used
`model_timeout` fallback, so that fallback observation is not their failure diagnosis.
Usage coverage is 7/12 and aggregate requests/tokens/billing remain unavailable.
See the [trial execution record](../../evidence/m5/20260908-staging-rag-trial-34166173023-execution.json)
and [original quality report](../../evidence/m5/20260908-staging-rag-trial-34166173023-quality.json).
The hosted path is verified; full M5/M7 quality and independent review remain open.

Use the exact run ID from dispatch, wait for completion, and keep its observed attempt:

```powershell
$ragRunId = Read-Host 'Evaluation workflow run ID'
gh run watch $ragRunId --repo $ragRepo --exit-status
```

A failed run is still worth inspecting. Retrieve its metadata after the watch returns,
including when it returns nonzero. Define the common verifier from the repository root
using the reviewed evaluator and v2 dataset. It checks report kind as well as integrity:

```powershell
$ragRunJson = gh run view $ragRunId --repo $ragRepo `
  --json workflowName,headSha,attempt,status,conclusion,url
if ($LASTEXITCODE -ne 0) { throw 'Could not read the selected run' }
$ragRun = $ragRunJson | ConvertFrom-Json
if ($ragRun.workflowName -ne 'Evaluate Staging RAG Quality' -or $ragRun.status -ne 'completed') {
  throw 'Select a completed RAG evaluation run'
}
$ragAttempt = $ragRun.attempt
$verifyRagReport = @'
import hashlib
import json
import sys
from pathlib import Path
from enterprise_doc_core.evaluation import verify_report_payload
from enterprise_doc_core.evaluation.rag_quality import load_rag_quality_dataset
from scripts.evaluate_staging_rag_quality import EVALUATOR_VERSION, select_rag_quality_cases
kind, scope = sys.argv[3:5]
if kind not in {"validation", "quality"} or scope not in {"trial", "full"}:
    raise SystemExit("Expected validation|quality and trial|full")
report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if not verify_report_payload(report):
    raise SystemExit("Report payload checksum mismatch")
if report["provenance"]["commit_sha"] != sys.argv[2]:
    raise SystemExit("Evaluator checkout SHA differs from the selected workflow run")
if report["provenance"]["working_tree_dirty"] is not False:
    raise SystemExit("Hosted evaluator checkout was not clean")
validation = kind == "validation"
suite = "staging-real-provider-rag-quality" + ("-validation" if validation else "")
execution_scope = (
    "local-dataset-validation" if validation else "authenticated-staging-real-provider-quality"
)
if report["suite"] != suite or report["evaluator_version"] != EVALUATOR_VERSION:
    raise SystemExit("Wrong report kind or evaluator version")
if report["provenance"]["environment"]["execution_scope"] != execution_scope:
    raise SystemExit("Wrong execution scope")
loaded = load_rag_quality_dataset(Path("evaluation/rag_quality_v2.json"))
selected = select_rag_quality_cases(loaded, trial_only=scope == "trial")
expected_ids = [case.case_id for case in selected]
for key, expected in {
    "dataset_version": loaded.dataset.version,
    "dataset_sha256": loaded.dataset_sha256,
    "corpus_sha256": loaded.corpus_sha256,
    "selected_case_count": len(expected_ids),
    "total_case_count": len(loaded.dataset.cases),
}.items():
    if report[key] != expected:
        raise SystemExit(f"Report {key} differs from the reviewed v2 selection")
input_hash = hashlib.sha256(
    f"{loaded.dataset_sha256}:{loaded.corpus_sha256}".encode("ascii")
).hexdigest()
if report["provenance"]["input_sha256"] != input_hash:
    raise SystemExit("Provenance input hash mismatch")
case_ids = report["selected_case_ids"] if validation else [case["case_id"] for case in report["cases"]]
if case_ids != expected_ids:
    raise SystemExit("Report cases differ from the reviewed selection")
if validation:
    if report["status"] != "passed" or "measured" in report:
        raise SystemExit("Invalid dataset-only report")
else:
    coverage = "bounded_sample" if scope == "trial" else "full"
    if report["trial_only"] != (scope == "trial") or report["coverage"] != coverage:
        raise SystemExit("Live report coverage mismatch")
    if report["status"] not in {"passed", "failed"}:
        raise SystemExit("Invalid live report status")
print(f"Verified {kind} report for {scope}; recorded status: {report['status']}")
'@
```

For the validation artifact, download and verify both exact files. A live dispatch also
has this artifact from its prerequisite job:

```powershell
$ragValidationDir = Join-Path $env:TEMP (
  "enterprise-doc-rag-validation-$ragRunId-$ragAttempt-" + [guid]::NewGuid().ToString('N')
)
gh run download $ragRunId --repo $ragRepo `
  --name "staging-rag-validation-$ragRunId-$ragAttempt" --dir $ragValidationDir
if ($LASTEXITCODE -ne 0) { throw 'Validation artifact unavailable; inspect the validate job' }
foreach ($ragScope in @('trial', 'full')) {
  $ragValidationPath = Join-Path $ragValidationDir "rag-validation-$ragScope-$ragRunId-$ragAttempt.json"
  uv run --no-sync python -c $verifyRagReport $ragValidationPath $ragRun.headSha validation $ragScope
  if ($LASTEXITCODE -ne 0) { throw 'Validation report verification failed' }
}
```

For an explicit live dispatch, verify the separate quality artifact against the scope
actually selected. Missing quality output is expected for validate-only runs; do not
download a validation artifact under the quality name:

```powershell
$ragScope = Read-Host 'Selected live evaluation scope (trial or full)'
if ($ragScope -notin @('trial', 'full')) { throw 'Expected trial or full' }
$ragEvidenceDir = Join-Path $env:TEMP (
  "enterprise-doc-rag-quality-$ragRunId-$ragAttempt-" + [guid]::NewGuid().ToString('N')
)
gh run download $ragRunId --repo $ragRepo `
  --name "staging-rag-quality-$ragRunId-$ragAttempt" --dir $ragEvidenceDir
if ($LASTEXITCODE -ne 0) { throw 'Quality report unavailable; inspect the evaluate job' }
$ragReportPath = Join-Path $ragEvidenceDir "rag-quality-$ragRunId-$ragAttempt.json"
uv run --no-sync python -c $verifyRagReport $ragReportPath $ragRun.headSha quality $ragScope
if ($LASTEXITCODE -ne 0) { throw 'Report verification failed' }
```

The seal verifies content integrity, not reviewer approval or a digital signature.
`provenance.commit_sha` identifies the evaluator checkout, not the deployed server.
Dataset SHA is the loader's canonical dataset hash, not a hash of the JSON file bytes.
The verifier accepts structurally valid failed quality reports for diagnosis; its own
success is not a model-quality pass. Local dirty-checkout validation remains local
evidence and intentionally fails the hosted clean-checkout requirement.
Retain the run URL/attempt, evaluator SHA, accepted staging release record/image digests,
dataset/corpus hashes, observed routes/behavior versions, and review outcome together.

| Result | Interpretation and next action |
| --- | --- |
| Validation suite, `status: passed`, 12/40 selections | Dataset/runtime preflight only. No staging/model execution or quality conclusion. |
| `status: passed`, `coverage: bounded_sample`, `trial_only: true`, 12/40 cases | Trial thresholds passed for the selected metrics. Review all case diagnostics before requesting the full run. |
| `status: passed`, `coverage: full`, `trial_only: false`, 40/40 cases | One synthetic v2 run met its thresholds. Verify clean provenance, dataset hashes and routes; repeatability, billing and human semantic review remain separate evidence. |
| `status: failed` with a sealed report | Preserve failed cases, applicable targets and diagnostic codes; resolve the cause before a new recorded execution. Do not lower thresholds or rerun to select only a pass. |
| Missing report, cancelled job, checkout/dependency error, HTTP error or deadline | Execution is incomplete. Inspect the failing step; never substitute a previous attempt's report or count this as a quality pass. |

HTTP 401 requires checking the short-lived token and active smoke membership. HTTP 403
requires distinguishing application authorization from an edge rejection; object-store
host rejection requires reviewing the exact presign host. Diagnose provider transport,
rate limits and timeouts separately from answer-quality failures. For incomplete runs,
record the run URL, failed step and bounded error classification; sanitize any log excerpt.

`cost_metadata.billing_amount` and `billing_currency` are currently always `null`;
token usage may be partial or unavailable. Associate reviewed provider rates/billing and
stable provider revision as separate evidence without modifying the sealed report.
The report also omits raw answers, so independent semantic review needs authorized access
to the same tenant's results. A green Actions conclusion alone cannot close M5/M7.

## v0.1.18 observation

Before the readiness cache was added, a 200-request/20-concurrency run completed all
requests but measured approximately 9.57 requests/second, P50 1.85 seconds, P95
3.86 seconds, and P99 4.32 seconds. The run had no host or dependency sampler and was
therefore recorded as a failed bounded baseline, not a production capacity result.

Release `v0.1.18` deployed the cache through staging run
`https://github.com/Drew-Z/enterprise-doc-agent/actions/runs/30964373803`. Migration,
workload rollout, the real embedding/reindex gate, in-cluster readiness smoke, and the
authenticated upload -> ingestion -> Agent smoke all passed on attempt 3. The first two
public readiness repetitions exposed a separate network boundary:

- QUIC run 1: 190/200 successful, P50 1.29 seconds, P95 13.77 seconds, and 10 transport
  timeouts;
- QUIC run 2: 175/200 successful, P50 1.59 seconds, P95 15.01 seconds, and 25 transport
  timeouts;
- an operator-only Pod forward completed 200/200 at approximately 389.82 requests/second,
  P50 22.86 milliseconds, P95 35.70 milliseconds, and P99 40.26 milliseconds;
- the host-to-loopback Traefik path completed 200/200 at approximately 160.36
  requests/second, P50 47.01 milliseconds, P95 82.79 milliseconds, and P99 111.94
  milliseconds.

API logs showed sub-millisecond cached handlers and no application request over 1.52
seconds in the first public failure window. Some timed-out requests arrived late or never
reached the API. `cloudflared` exposed four QUIC connections to LAX with roughly 416-471
millisecond smoothed RTT and timeout packet loss. This isolated the dominant tail to the
Tunnel/edge path rather than Kubernetes, Traefik, API CPU/memory, or dependency probes.

The reviewed host drop-in in
`infra/host/ubuntu-24.04/systemd/cloudflared.service.d/transport.conf` changed only the
Tunnel transport to HTTP/2. Two repetitions then completed 200/200 with no transport
errors:

- HTTP/2 run 1: 13.65 requests/second, P50 1.20 seconds, P95 2.54 seconds, and P99 2.86
  seconds;
- HTTP/2 run 2: 16.25 requests/second, P50 1.01 seconds, P95 1.83 seconds, and P99 2.44
  seconds.

Both reports still have `status: failed` because their public P95 exceeds the script's
250-millisecond local target. Preserve that result: the change removes errors and improves
the bounded baseline, but it does not establish a public SLO or production capacity. The
raw reports remain outside the repository under the operator's temporary evidence path.

## Escalation boundaries

- A readiness failure blocks rollout and requires dependency diagnosis.
- A high readiness latency with healthy dependencies should first be checked against
  cache age, object-store latency, and database pool utilization.
- Real upload -> ingestion -> retrieval -> Agent validation remains the authoritative
  business-path smoke; the readiness load does not replace it.
- Managed retention, alert routing, backup/restore, and representative capacity remain
  external gates. The local PVC is a staging diagnostic aid, not a managed telemetry or
  disaster-recovery claim.
