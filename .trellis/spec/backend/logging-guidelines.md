# Logging Guidelines

## Format

API and Worker configure the standard Python logging root with `JsonFormatter`.
Application log lines are JSON and contain `timestamp`, `level`, `service`,
`environment`, and `event`. Request-scoped logs also contain `request_id`
and `correlation_id`.

## Levels

- `INFO`: process lifecycle and completed requests.
- `WARNING`: expected dependency timeout or unavailable state.
- `ERROR` through `logger.exception`: unexpected request/process failure.

## Required Context

Use `extra={"event_data": {...}}` for structured event fields. Log component name,
status code, duration, and error class where relevant. Optional principal fields are
emitted only after a real principal resolver enriches the request context.

## Secret Safety

`sanitize_log_value` recursively redacts secret-aware values and keys containing
authorization, cookie, DSN, password, secret, signature, or token markers. Never log
request/response bodies, document text, prompt text, credentials, signed URLs, or raw
connection strings.

M1 extends the sensitive field set to object-store upload IDs, object keys, filenames,
checksums, and SHA-256 values. Log messages are stable event names: parameterized
messages are redacted, and arbitrary objects are represented by type only instead of
calling `str()` on exceptions or dependency responses.

M4 also forbids raw model input/output, retrieved evidence text, tool arguments/results,
execution-context tokens, approval fingerprints/comments, checkpoint payloads, artifact
object keys, and download signatures. Agent events use per-event allowlists; tool audit
rows persist hashes and bounded summaries instead of raw bodies.

## Scenario: Preserve Application Logging Through Celery Startup

### 1. Scope / Trigger

The consumer configures the application root logger before Celery starts. Celery's
default root-logger takeover replaces its formatter, losing `event_data` and exposing
exception bodies through the default traceback formatter.

### 2. Signatures

`configure_logging(service="worker-consumer", environment=..., level=...)` precedes
`create_celery_app(settings)` and `app.worker_main(...)` in `consumer_main.main`.
`create_celery_app` sets `worker_hijack_root_logger=False`.

### 3. Contracts

Preserve the configured root handler and `JsonFormatter` through Celery logging setup.
Application failures retain stable event names and bounded `event_data`, including
`diagnostic_code`, while `exc_info` contributes only the exception type. This contract
covers application logging; Celery banners and its own task logger have separate setup.

### 4. Validation & Error Matrix

- Application error after startup -> one structured JSON record with safe context.
- Exception body/traceback -> absent from application log output.
- Secret-key event field -> `**********` while the diagnostic category remains readable.

### 5. Good/Base/Bad Cases

- Good: `agent_execution_handler_failed` retains `agent.unexpected.runtime_error`.
- Base: startup requires no broker or model connection to verify logging behavior.
- Bad: testing only the configured flag misses framework changes that replace the formatter.

### 6. Tests Required

`test_celery_startup_preserves_structured_redacted_application_errors` runs actual
`app.log.setup(loglevel="INFO", redirect_stdouts=True)` in an isolated subprocess.
Assert the JSON event, service, type, diagnostic and redacted synthetic secret; reject
the synthetic exception body and `Traceback`. Do not substitute an in-memory stream that
misses Celery's writes to the process's original stderr.

### 7. Wrong vs Correct

Wrong: rely on Celery's default `worker_hijack_root_logger=True` after configuring JSON.
Correct: configure application logging first and preserve it with
`app.conf.update(worker_hijack_root_logger=False)`; verify actual emitted output.

## Scenario: Attribute a Consumer Attempt to Its Runtime

### 1. Scope / Trigger

A shared configured worker label cannot distinguish consumers or restarts. A later
import probe proves only what that new process imports. Attribute new durable attempts
using the executing consumer's identity and confirmed lifecycle events.

### 2. Signatures

`consumer_main.main()` creates one identity from at most 167 characters of the configured
worker label, `-`, and a 32-character UUID4 hex suffix. Supply the same identity to
`build_consumer_app(..., worker_id=...)` and `consumer_worker_argv(..., worker_id=...)`.
A directly constructed consumer app generates an identity when none is supplied.
The existing `JobAttempt.worker_id` / Job lock columns remain sufficient; settings and
the separate probe/publisher process retain the configured label.

### 3. Contracts

| Event | Emission point | Event-specific context |
|---|---|---|
| `worker_consumer_configured` | Consumer composition completed | `worker_id`, actual `process_id`, `hostname` |
| `job_attempt_claimed` | `JobRuntimeService.claim` returned a real claim after transaction completion | `job_ref`, `attempt_ref`, `attempt_number`, `job_type`, `worker_id` |
| `agent_execution_handler_failed` | An unexpected executor exception is classified, before Job failure settlement | `run_ref`, `execution_ref`, `job_ref`, `attempt_ref`, `worker_id`, execution kind and existing safe error/diagnostic/type/count fields |
| `job_attempt_failure_recorded` | Handler failure settlement returned successfully from `JobRuntimeService.fail` | Claim fields plus returned `job_status`, `error_code`, `error_type`, allowlisted `diagnostic_code` or null |

`*_ref` values are lowercase SHA-256 hex digests of canonical UUID text encoded as UTF-8.
These approved identifier references are distinct from sensitive document checksums;
their field names deliberately avoid the redactor's `sha256` marker. They are stable
correlation references, not authorization credentials. Exclude raw business IDs, job
payloads, lease/fencing tokens, model failure metadata and exception bodies from these
events. Keep per-runtime and per-attempt values out of Prometheus labels.

The configuration event does not prove that a task executed. The Agent-handler error
event does not prove persistence. Failure-recorded covers the handler settlement path;
it does not promise an event for every cancellation, heartbeat error, forced termination,
or failed log sink. Event construction/emission is best effort: logging failures must
not change execution, retry classification, the original diagnostic or durable settlement.

### 4. Validation & Error Matrix

- Two launches from one label -> distinct identities, each within 200 characters.
- Successful claim -> one claim event using authoritative claim data.
- Duplicate/not-claimable delivery -> no fabricated claim or failure-recorded event.
- Handler failure + successful settlement -> safe diagnostic and actual returned Job status.
- Settlement raises (including lease loss) -> no failure-recorded event.
- Log handler raises -> original business outcome and exception cause remain intact.
- Raw IDs, synthetic private text, traceback or lease/fencing secrets -> absent from JSON.

### 5. Good/Base/Bad Cases

- Good: match one consumer configuration and hashed attempt references to a local durable row.
- Base: direct registered-adapter execution with a checkpoint failure before provider/MCP access.
- Bad: infer an old attempt's executing process from a new import probe or a shared worker label.

### 6. Tests Required

`apps/worker/tests/test_consumer_main.py` covers identity propagation, label length and
configuration-log failure. `test_queue.py` covers confirmed event timing, duplicate and
failed settlement, bounded metrics and sink isolation. `test_agent_handler.py` checks
hashed run/execution/claim references and preservation of the original diagnostic when
the log handler raises; existing cancellation and typed diagnostics remain covered.

`tests/agent/test_consumer_attempt_attribution_integration.py` uses an isolated subprocess,
actual Celery logging setup, consumer composition and registered task adapter, and an
explicit loopback PostgreSQL database. Seed, execute and read back on one AsyncTaskRunner
loop. Inject checkpoint failure and assert zero gateway/MCP calls, one durable attempt,
matching runtime/references/diagnostic and safe emitted JSON. This is local composition
evidence; it is not a Redis delivery test, staging acceptance or historical root-cause proof.

### 7. Wrong vs Correct

Wrong: log raw run/execution UUIDs or let a failing log sink replace the original error.
Correct: project approved hashed references, guard log emission, and preserve the existing
exception classification and persistence path. A future staging check must collect a new
authorized attempt and bind its identity to the reviewed source/image and consumer process.

## Proven Examples

- `packages/core/src/enterprise_doc_core/logging/json.py`
- `apps/api/src/enterprise_doc_api/middleware/request_context.py`
- `packages/core/tests/test_logging.py`
- `apps/api/tests/test_request_context.py`
- `apps/worker/tests/test_consumer_logging.py`
- `apps/worker/src/enterprise_doc_worker/consumer_main.py`
- `apps/worker/src/enterprise_doc_worker/queue.py`
- `tests/agent/test_consumer_attempt_attribution_integration.py`
