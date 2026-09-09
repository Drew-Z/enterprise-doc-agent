# Consumer attempt attribution: implementation

## Dependencies and authorization

- Previous child `09-09-staging-smoke-failure-evidence` passed local validation and remains in review pending the separate commit gate.
- The user's continuous-main-task request authorizes this local implementation and local synthetic tests. Historical diagnosis and release gates remain independent.
- Read the parent/M5 designs, Worker and logging/database/quality specs, exact consumer/queue/settings source, existing unit tests and local integration helpers before code.

## Ordered execution

- [x] Converge PRD/design from current source and create the M5 child.
- [x] Red/green: per-launch identity used by both consumer composition and Celery hostname, with column-length compatibility.
- [x] Red/green: confirmed claim/failure events with bounded hashes and safe diagnostic metadata.
- [x] Red/green: duplicate, settlement failure, and logger failure behavior.
- [x] Add/execute the real registered-adapter/local-database/logging regression with provider/MCP calls forbidden.
- [x] Run related Worker/Agent/jobs regressions plus full non-integration, Ruff, mypy and diff/context checks.
- [x] Record validation, update adopted logging spec, mark local review, and plan the next child.

## Owned files

`apps/worker/src/enterprise_doc_worker/{consumer_main,queue,config,agent_handler}.py`, related Worker unit tests, one new `tests/agent/test_consumer_attempt_attribution_integration.py`, adopted logging spec, and Trellis task/parent artifacts. `agent_handler.py` was added after the real integration regression exposed the existing raw-ID logging boundary; only that error-event projection and logging isolation are changed. Core storage and Kubernetes files are reference-only.

## Validation

Use the existing `.venv` Python with `-X utf8 -B`, pytest `-p no:cacheprovider` and a self-cleaning system temporary `--basetemp`.

- Focused Worker tests: consumer main/logging/queue/Agent handler.
- Local integration: new attribution case, existing Agent failure diagnostics, Agent run and durable Job runtime tests; explicitly loopback PostgreSQL, no real providers.
- Required: `pytest -m "not integration" -q --tb=short`; Ruff format/check; mypy for Core/API/Worker/MCP.
- Validate this task's context manifests, original 86-file archive hashes, first-child code hashes and real Git index/staged paths.

Record actual executed argv, timestamps and outcomes in `validation.json`. Do not broaden to live staging, image builds, trial reruns or foreign worktrees.

## Local execution result — 2026-09-09

Per-launch identities, confirmed attempt events and the Agent-handler error projection
are implemented. The registered-adapter regression first exposed raw run/execution IDs;
12 diagnostic cases and one broken-sink case then reproduced the handler boundary before
the fix. The final focused Worker gate passed 51 tests, the new local adapter regression
passed 1, and related Agent/Job integration regressions passed 21. After formatting the
owned source/tests, the full non-integration gate passed 1086 tests (127 integration cases
deselected), mypy passed for 161 source files, and Ruff lint/format checks passed.

A read-only subagent review was interrupted after approximately five minutes without a
returned report; no independent findings are claimed. The primary review confirmed the
production console entrypoint, factory/claim identity propagation, transaction-return
event timing and JsonFormatter projection against the actual sources. Historical failure
attribution remains unresolved. [Validation](validation.json) records final commands,
source hashes, context/diff checks and cleanup. All 86 original archive files, first-child
source hashes, HEAD and the real Git index were unchanged; the staged set was empty.
The child is in `review` with local validation passed, pending the separate commit gate.

The active session has switched to [delivery and staging acceptance planning](../09-09-delivery-staging-acceptance-plan/prd.md), cycle 3 under M6. That child prepares the exact local commit batch and future evidence gates; it does not execute a deployment.
