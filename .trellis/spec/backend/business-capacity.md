# Local business capacity sampler

## 1. Scope / Trigger

Use this contract for `scripts/business_capacity*.py`, the local CLI, its plans and
tests. It measures upload, ingestion, Core retrieval and background Presales
generation/recovery through existing contracts. It does not add a product API or
produce production acceptance evidence for `run_application_capacity.py`.

## 2. Signatures

```powershell
.venv\Scripts\python.exe -B -X utf8 -m scripts.run_business_capacity --plan infra/capacity/business-capacity.example.json
# Execution additionally requires --execute-local --confirm-controlled-target
# and --output-dir <new absolute directory outside the repository>.
```

Module interfaces: `load_business_plan(Path) -> LoadedBusinessPlan`,
`run_business_matrix(loaded, token, observer, *, io=None, on_sample=None) -> dict`,
and `execute_local(loaded, output, settings) -> dict`. The observer implements
`ingestion(version_id, case)`, `retrieve(version_id, case)` and
`ledger(row_id, attempt_id)` using real Core services and tenant-scoped queries.

## 3. Contracts

- Plan version is the integer `1` (not `true` or `1.0`); unknown fields are rejected.
  One or two repetitions contain ordered ramp, steady_state, burst, recovery phases.
  At most 160 tasks, concurrency 4, 8 cases, 1 MiB per fixture, 16 upload parts,
  20,000 explicit HTTP requests and 7,200 seconds per matrix. All durations must
  be finite and positive. Inputs are confined to the plan directory and checked
  against exact size/SHA-256 before network access.
- `base_url` and `object_origins` accept only loopback origins. API requests carry
  a bearer token; signed PUTs use a separate client without that token. Validate
  the full signed origin, part number/size/hash and sensitive headers. Disable
  redirects and environment proxies; limit responses to 2 MiB and apply an
  overall request deadline, including streamed bodies. The HTTP count includes
  CLI quota preflight, but excludes observer DB/object calls and server workers.
- CLI reads `ApiSettings(_env_file=None)`: settings must be supplied explicitly in
  environment variables. `APP_ENV=local/test`, `MODEL__PROVIDER=deterministic`,
  `EMBEDDING__PROVIDER=hash`, local DB/Redis/object endpoints and a token named by
  `token_env` (default `ENTERPRISE_DOC_LOAD_TOKEN`) are required. Resolve the JWT
  and owner membership in the database, compare HTTP tenant identity and quota,
  and reject pre-existing documents or Presales packets. The confirmation flag
  is an operator declaration about the actual API/worker model configuration;
  local settings alone do not attest that remote process configuration.
- Each upload is followed through inventory, succeeded processing job, active
  generation, chunk/embedding counts, object SHA readback and document quota.
  `ProductUsageEvent` carries its reservation ID, not quantity; quantity comes
  from `ProductUsageReservation`. Require one consume event for that reservation.
- Core retrieval occurs **in the observer process**. Check tenant/version,
  source anchor and active generation; do not label it server HTTP latency or
  generation's internal retrieval latency. Hash embeddings make supplier wait
  inapplicable; preserve `null`, not a fabricated zero.
- Background acceptance and terminal latency are separate. A new client replays
  the same key immediately and after completion; require one attempt and an
  unchanged result. Check provider call count, reservation consumption/release,
  citations, one review revision, refreshed state and CSV. No changed-key retry.
- Reports contain every planned task, per-boundary timing/status, case/phase/round
  summaries and resource UUIDs. Expected double-route failure is
  `expected_failure`, `business_success=false`, `expectation_met=true`.
  Cancellation preserves active `interrupted` tasks and later `not_run` tasks;
  TaskGroup drains cancellations before report assembly. Journal failures force
  `interrupted` even when completed business assertions passed.
- `local_checks_passed` means all expected business assertions passed. Proposed
  p95 targets are diagnostic, not part of this status. Every report has
  `production_capacity_approved=false`. CLI outputs `run.json`, `plan.json`, and
  flushed `samples.jsonl`; failed preflight records `tasks_not_started` and
  `preflight_completed=false`. Exit 2 is configuration rejection; exit 1 is a
  failed/interrupted run; exit 0 is dry-run or passed local assertions.
- CLI closes its own clients; it does not delete documents, stop accepted jobs,
  or clear queues. Keep resource refs for operator inspection after interruption.
  Reports exclude tokens, signed URLs, document text and upstream exception text.

## 4. Validation & Error Matrix

| Condition | Result |
| --- | --- |
| Remote origin, invalid number/order/budget/version | `invalid_business_plan` |
| Changed bytes or outside-plan fixture | `fixture_integrity` / `fixture_path` |
| Existing or repository-local output directory | `output_directory_rejected`, no connection |
| Uncontrolled local settings / absent confirmation | Configuration rejection |
| Non-owner, existing business data, insufficient quota | Interrupted preflight; no submitted task |
| Wrong signed origin/headers or object redirect | `object_request_rejected` / `http_307` |
| Slow stream or stage/task deadline | `request_timeout` / `boundary_timeout` |
| Request/run budget exhausted before submission | `not_run`; retain full denominator |
| Changed replay, incorrect ledger, missing evidence | Failed boundary; never a passed sample |
| Resource construction, journal or cancellation failure | Sanitized interrupted report |

## 5. Good / Base / Bad Cases

Good: isolated PostgreSQL schema + actual MinIO/Redis/Celery/API, Hash embeddings
and an HTTP-controlled provider; TXT normal, PDF primary 503/fallback success,
DOCX both routes 503. Retain 7 successes and 3 expected failures per 10 tasks.

Base: committed TXT example validates without credentials/network and runs only
against a separately prepared local background-generation target.

Bad: passing this report to the production evidence gate, claiming browser outage
recovery from a fresh HTTP client, or removing failures from latency/quality data.

## 6. Tests Required

- `tests/deployment/test_business_capacity.py`: fixed bytes, plan validation,
  request separation, rejected signed origins/headers/redirects, slow streams,
  request/task/run limits, cancellation denominators, CLI preflight failure,
  dry-run and overwrite protection.
- `tests/presales/test_business_capacity_integration.py`: both ASGI transport and
  real loopback Uvicorn plus a separate CLI process; actual JWT/database/storage/
  ingestion, all three file/fault cases, once-only accounting/review and CSV.
  CLI journal equals final samples and a second run rejects the nonempty tenant.
  Only provider HTTP and embeddings are controlled; cleanup must preserve the
  sentinel tenant job and remove only owned test resources.
- Ruff, strict Mypy including the three scripts and project source roots, full
  non-integration regression, then final relevant integration. Preserve failed
  attempts separately from successful reruns.

## 7. Wrong vs Correct

Wrong: count 10/10 successful generations because the fault assertions passed;
report 202 latency as completion latency and local HEAD as the deployed version.

Correct: report 7/10 business successes, 3/10 expected failures and 10/10 assertions;
retain acceptance/terminal timings and executor source hashes separately. Current
4C4G telemetry, deployed identity, approved targets and independent review remain
required before any production capacity claim.

## Proven Examples

- `scripts/run_business_capacity.py`
- `infra/capacity/business-capacity.example.json`
- `tests/deployment/test_business_capacity.py`
- `tests/presales/test_business_capacity_integration.py`
