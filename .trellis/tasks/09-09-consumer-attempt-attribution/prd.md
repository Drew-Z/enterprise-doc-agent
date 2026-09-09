# Consumer attempt attribution

## Goal

Bind a new consumer runtime to its durable attempts and sanitized failure events so a later investigation can distinguish actual execution from a separate import probe. Implements a bounded part of M5-R1/R2/R4/R16 and parent DR-10.

## Confirmed Facts

- `apps/worker/src/enterprise_doc_worker/consumer_main.py:76` originally passes the configured `worker-local` default into every consumer. Its Celery hostname includes `%h` at line 99, while durable history receives only the shared configured value.
- `packages/core/src/enterprise_doc_core/jobs/models.py:177` already stores `worker_id` as `String(200)`. `jobs/service.py:514` commits that identity into Job lock ownership, JobAttempt and the claim; fencing compares the same value at line 870.
- `apps/worker/src/enterprise_doc_worker/queue.py:148` claims before running a handler but has no structured attempt correlation event. Handler failures are durably settled at line 294.
- Existing tests separately exercise logging startup (`apps/worker/tests/test_consumer_logging.py:9`) and a manually constructed consumer with a real local database (`tests/agent/test_agent_failure_diagnostics_integration.py:58`). They do not yet join the registered production consumer adapter, actual process identity, JSON log output and durable attempt in one regression.
- The first continuous child [passed local validation](../09-09-staging-smoke-failure-evidence/validation.json). Its evidence reports do not establish the cause of historical v0.1.34 QA failure.
- The first registered-adapter integration run matched process/attempt/diagnostic but failed the raw-ID assertion: `agent_handler.py:477` still logged raw run/execution IDs. Its unguarded logging at line 473 can also replace the original diagnostic if the sink raises. Include this exact handler logging boundary in the child.

## Requirements

- **CAA-R1**: Each production consumer launch creates a fresh random worker identity, keeps the configured worker label as a bounded prefix, and uses the exact same value for Celery hostname and all claims from that runtime. The identity fits the existing 200-character column and does not mutate settings or publisher identity.
- **CAA-R2**: Consumer composition emits a stable configuration event containing the runtime identity, process PID and host name. It describes configuration, not proof of task execution.
- **CAA-R3**: Emit a claim event only after a real claim and a handler-failure-recorded event only after durable failure settlement succeeds. Correlate them with SHA-256 `job_ref`/`attempt_ref`, attempt number and worker identity; retain the safe diagnostic and actual returned job status. Duplicate deliveries or failed settlement must not create a false recorded-attempt event.
- **CAA-R4**: Exclude raw business IDs, lease/fencing secrets, payloads, model output, exception messages and failure metadata from the new events and the existing Agent-handler failure event on this path. Replace its run/execution fields with hashed references and bind its job/attempt references to the claim. Keep all runtime/attempt IDs out of Prometheus labels, and keep logging failure from changing business execution, the original diagnostic, or settlement.
- **CAA-R5**: A local integration regression enters the real registered Celery adapter and consumer composition, injects a checkpoint failure before model/MCP access, and matches the persisted worker/attempt/diagnostic to real JSON logs after Celery logging setup. It must explicitly use loopback local database settings, prohibit provider/MCP execution, and distinguish direct task-adapter invocation from a broker-delivery or staging test.

## Acceptance Criteria

- [x] Two launches from the same configured label produce distinct bounded identities; one launch uses the same value in hostname and durable claims.
- [x] Real emitted configuration/claim/failure JSON correlates with the local durable attempt while raw identifiers, payloads, secrets and exception text remain absent.
- [x] Duplicate delivery and failed durable settlement do not fabricate recorded failure events; logger failure leaves business outcomes unchanged.
- [x] Existing claim/cancellation/fencing/diagnostic tests and the bounded local integration regression pass; required non-integration, Ruff and mypy gates pass.
- [x] Record commands, code hashes, local-only limitations and cleanup; update the parent and select the next evidence-backed child.

## Out Of Scope

New database columns or migrations, Kubernetes/remote changes, new broker/route/retry semantics, publisher identity changes, real provider requests, backfilling old attempts, or declaring the historical cause known. Existing failure archives remain byte-identical. Commit/archive and external release execution retain their existing separate gates.
