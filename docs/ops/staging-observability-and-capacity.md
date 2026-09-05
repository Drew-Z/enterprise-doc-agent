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
`evaluation/rag_quality_v2.json` corpus. It runs only on the repository-scoped staging
runner, targets the `staging` Environment, and shares the
`enterprise-doc-agent-staging` concurrency lock with deployment and rollback. The
Environment name alone does not configure GitHub deployment protections. The job does
not load Kubernetes credentials or issue cluster commands; it calls the
public HTTPS control plane with the short-lived `STAGING_SMOKE_TOKEN` Environment
secret.

The dispatch menu defaults to `trial`, which selects the twelve explicitly marked v2
cases. Choose `full` only after the provider route, revision, provider billing inputs,
approved corpus scope, and human reviewer are available; it selects all 40 v2 cases.
Each attempt uploads only its exact `rag-quality-<run-id>-<attempt>.json` file, even if
the runner retains older files in the same temporary directory. The report carries hashed
queries and answers, route/behavior identities, aggregate token telemetry, and quality
diagnostics, not bearer tokens, document bodies, artifact URLs, or raw model output.

A passing workflow proves only that selected evaluation completed against the observed
route. It does not by itself close M5/M7: the full quality gate also requires stable
provider revision and cost metadata, representative-corpus review, and independent
human semantic approval. Do not run the public-reference-inspired synthetic suite
through this workflow or represent it as provider-quality evidence.

### Preconditions and configuration

Use the [4C4G runbook](single-node-4c4g-staging-runbook.md) for the current host.
The shared provisioning procedures in the tiny runbook remain useful, but its historical
configured/missing inventory is not a current Environment audit. Check the following
before each evaluation window:

| Item | Required value or observation |
| --- | --- |
| Workflow revision | The workflow is published on the remote default branch; the selected branch/tag contains the reviewed evaluator and frozen dependencies. Local commits cannot be dispatched. |
| Environment | Existing `staging` Environment with its allowed refs and review protection checked; dispatch only a reviewed ref. |
| Runner | Repository-scoped, non-root runner online with labels `self-hosted`, `linux`, `x64`, `enterprise-doc-staging`. |
| Toolchain and network | Python 3.12 and uv 0.11.3 at the workflow's `/opt/enterprise-doc-toolchain/python/bin/` paths; Git checkout and frozen dependency sync reachable. |
| `STAGING_ALLOWED_HOST` | Environment variable `agent.playlab.eu.cc`, a hostname without a scheme or path. |
| `STAGING_OBJECT_STORE_ALLOWED_HOST` | Environment variable containing the exact presigned-upload object-store hostname, without a scheme or path. |
| `STAGING_SMOKE_TOKEN` | Environment secret for the dedicated active synthetic smoke tenant/user; valid through approval/queue delay and the evaluation window. |
| Staging release | Latest accepted release record and immutable image identities retained; readiness and authenticated business smoke healthy. |

Rotate the token through the existing administrator-operated
[smoke token procedure](tiny-staging-runbook.md#first-rollout), immediately
before the window. The evaluator step alone receives that secret. It does not mint
tokens, provision membership, change model routes, or reindex existing documents.

Both `trial` and `full` upload the selected synthetic documents, run ingestion/embedding,
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

### Validate and dispatch from PowerShell

From the repository root, these commands validate both fixed selections without using a
token or calling staging, embedding or chat providers. Expect 12 and 40 selected cases:

```powershell
uv run python scripts/evaluate_staging_rag_quality.py `
  --dataset evaluation/rag_quality_v2.json --validate-only --trial-only
if ($LASTEXITCODE -ne 0) { throw 'Trial dataset validation failed' }
uv run python scripts/evaluate_staging_rag_quality.py `
  --dataset evaluation/rag_quality_v2.json --validate-only
if ($LASTEXITCODE -ne 0) { throw 'Full dataset validation failed' }
```

After the reviewed workflow is available remotely and the prerequisites above hold:

```powershell
$ragRepo = 'Drew-Z/enterprise-doc-agent'
gh workflow view evaluate-staging-rag-quality.yml --repo $ragRepo --ref main --yaml
if ($LASTEXITCODE -ne 0) { throw 'Reviewed remote workflow is not available' }
gh workflow run evaluate-staging-rag-quality.yml --repo $ragRepo --ref main `
  -f evaluation_scope=trial -f staging_base_url=https://agent.playlab.eu.cc
if ($LASTEXITCODE -ne 0) { throw 'Trial dispatch failed' }
```

Retain the returned run URL/ID and verify its commit in Actions. If the CLI does not
return a URL, locate the exact dispatch by workflow, actor, ref and start time in Actions;
do not assume the newest repository run is yours. After reviewing the trial and full-run
prerequisites, dispatch the same reviewed ref with `-f evaluation_scope=full`. Preserve
each attempt independently; a later pass does not erase a failed attempt.

### Retrieve and verify the report

Use the exact run ID from dispatch, wait for completion, and keep its observed attempt:

```powershell
$ragRunId = Read-Host 'Evaluation workflow run ID'
gh run watch $ragRunId --repo $ragRepo --exit-status
```

A failed run is still worth inspecting. Retrieve its metadata and artifact separately
after the watch command returns, including when it returns a nonzero exit code:

```powershell
$ragRunJson = gh run view $ragRunId --repo $ragRepo `
  --json workflowName,headSha,attempt,status,conclusion,url
if ($LASTEXITCODE -ne 0) { throw 'Could not read the selected run' }
$ragRun = $ragRunJson | ConvertFrom-Json
if ($ragRun.workflowName -ne 'Evaluate Staging RAG Quality' -or $ragRun.status -ne 'completed') {
  throw 'Select a completed RAG evaluation run'
}
$ragAttempt = $ragRun.attempt
$ragEvidenceDir = Join-Path $env:TEMP (
  "enterprise-doc-rag-$ragRunId-$ragAttempt-" + [guid]::NewGuid().ToString('N')
)
gh run download $ragRunId --repo $ragRepo `
  --name "staging-rag-quality-$ragRunId-$ragAttempt" --dir $ragEvidenceDir
if ($LASTEXITCODE -ne 0) { throw 'Report unavailable; inspect the failed workflow steps' }
$ragReportPath = Join-Path $ragEvidenceDir "rag-quality-$ragRunId-$ragAttempt.json"
$verifyRagReport = @'
import json
import sys
from pathlib import Path
from enterprise_doc_core.evaluation import verify_report_payload
report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if not verify_report_payload(report):
    raise SystemExit("Report payload checksum mismatch")
if report["provenance"]["commit_sha"] != sys.argv[2]:
    raise SystemExit("Evaluator checkout SHA differs from the selected workflow run")
print("Report checksum and evaluator checkout SHA verified")
'@
uv run python -c $verifyRagReport $ragReportPath $ragRun.headSha
if ($LASTEXITCODE -ne 0) { throw 'Report verification failed' }
```

The seal verifies content integrity, not reviewer approval or a digital signature.
`provenance.commit_sha` identifies the evaluator checkout, not the deployed server.
Retain the run URL/attempt, evaluator SHA, accepted staging release record/image digests,
dataset/corpus hashes, observed routes/behavior versions, and review outcome together.

| Result | Interpretation and next action |
| --- | --- |
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
