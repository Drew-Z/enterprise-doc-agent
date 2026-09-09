# Consumer attempt attribution: design

## Runtime identity

The consumer composition root owns identity generation, not domain services or model code. Build a worker identity from at most 167 characters of `settings.worker.worker_id`, a separator and one 32-character UUID4 hex suffix. `main` generates it once and supplies it to both `build_consumer_app` and `consumer_worker_argv`. A separately constructed consumer app also generates one identity if none is injected. Keep the existing tuple return shape and optional injection for deterministic composition tests. Settings continue to hold the configured label; the publisher process remains unchanged.

The existing durable `worker_id` field carries this value through Job lock ownership and JobAttempt. No schema expansion, migration, routing change or lease-rule change is needed. UUID uniqueness is per consumer runtime, including restart on the same host/PID. A configuration log records the exact runtime identity, local PID and hostname; its event name must not imply that a job has run.

## Attempt correlation events

At `JobDeliveryConsumer.handle`, emit `job_attempt_claimed` only after `runtime.claim` returns a real `ClaimedJob`. Use authoritative claim data rather than queue payload data. The new event projection includes only `job_ref` and `attempt_ref` (SHA-256 of canonical UUID text), attempt number, bounded job type and worker ID. The names intentionally avoid the logging redactor's sensitive `sha256` key marker; their digest semantics are documented here and in the adopted logging contract.

After `_settle_handler` receives the successful return from `runtime.fail`, emit `job_attempt_failure_recorded` with the same references, safe error class/code, allowlisted diagnostic or null, and the actual returned Job status. Do not claim persistence when `runtime.fail` raises; do not emit a new attempt on duplicate delivery. This event covers handler failure settlement, not every cancellation/heartbeat/forced-termination path.

Log projections never include raw IDs, payloads, lease tokens, fencing tokens, arbitrary exception attributes or model failure metadata. Logging is best effort and cannot become a new execution dependency. Metrics retain their existing finite labels; per-attempt references stay in structured logs.

The registered-adapter regression exposed raw `run_id`/`execution_id` in the older `agent_execution_handler_failed` event. Bring this event into the same contract with `run_ref`, `execution_ref`, `job_ref`, `attempt_ref` and runtime worker identity. Preserve all original diagnostic/classification semantics. Guard the logging call so a sink failure cannot replace the exception that must be wrapped and persisted. This is a locally proven logging boundary, not an explanation of the historical staging failure.

## Offline and local integration proof

Unit tests verify runtime identity propagation, bounded length, structured event values, duplicate/failed-settlement behavior and log failure isolation. Preserve existing cancellation and metrics tests.

The integration case uses an isolated Python subprocess, real `configure_logging` and Celery `app.log.setup`, real `build_consumer_app`/registered task adapter/AsyncTaskRunner, and the existing explicitly local PostgreSQL schema. Seed synthetic context through the same AsyncTaskRunner loop used by the task to avoid cross-loop connection reuse. Inject `UnavailableCheckpoint` plus gateways that fail immediately if model/MCP execution is attempted. Invoke the registered task directly; read back the attempt on that same loop and compare its hashed references and identity with captured JSON stderr. This proves composition, persistence and log correlation, not actual Redis delivery, Kubernetes deployment, or recovery of missing historical logs.

Close resources, runner and Celery app in `finally`; retain only sanitized aggregate assertions. Test-generated synthetic database records follow the repository's existing integration-fixture convention. No existing local data or artifacts are deleted.

## Release and rollback

Future deployment must bind the reviewed code/image identity to emitted consumer configuration and a new authorized attempt. Old `worker-local` rows remain valid historical records. Reverting these additive logs and composition identity generation does not require database rollback. The next child should prepare a concrete delivery/acceptance plan using completed local evidence before any new registry or staging action.
