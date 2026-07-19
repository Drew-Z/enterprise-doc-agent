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

M1 extends the sensitive field set to object-store upload IDs, object keys, filenames,
checksums, and SHA-256 values. Log messages are stable event names: parameterized
messages are redacted, and arbitrary objects are represented by type only instead of
calling `str()` on exceptions or dependency responses.

M4 also forbids raw model input/output, retrieved evidence text, tool arguments/results,
execution-context tokens, approval fingerprints/comments, checkpoint payloads, artifact
object keys, and download signatures. Agent events use per-event allowlists; tool audit
rows persist hashes and bounded summaries instead of raw bodies.

## Proven Examples

- `packages/core/src/enterprise_doc_core/logging/json.py`
- `apps/api/src/enterprise_doc_api/middleware/request_context.py`
- `packages/core/tests/test_logging.py`
- `apps/api/tests/test_request_context.py`
