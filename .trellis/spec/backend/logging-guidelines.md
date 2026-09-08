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

## Proven Examples

- `packages/core/src/enterprise_doc_core/logging/json.py`
- `apps/api/src/enterprise_doc_api/middleware/request_context.py`
- `packages/core/tests/test_logging.py`
- `apps/api/tests/test_request_context.py`
- `apps/worker/tests/test_consumer_logging.py`
