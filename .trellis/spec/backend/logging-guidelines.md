# Logging Guidelines

## Format

API and Worker configure the standard Python logging root with `JsonFormatter`.
Every line is JSON and contains `timestamp`, `level`, `service`,
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

## Proven Examples

- `packages/core/src/enterprise_doc_core/logging/json.py`
- `apps/api/src/enterprise_doc_api/middleware/request_context.py`
- `packages/core/tests/test_logging.py`
- `apps/api/tests/test_request_context.py`
