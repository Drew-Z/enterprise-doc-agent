# Error Handling

## Boundary Rules

Expected dependency failures are converted at the health boundary into typed
`down` or `timeout` component states. The API and Worker map any non-ready
aggregate to HTTP 503 with the same `ReadinessResponse` schema used by HTTP 200.

Unexpected application exceptions are logged by error class and re-raised. Response
payloads and logs do not include DSNs, credentials, stack traces, or document bodies.

## Patterns

- Use injected protocols or factories so tests can trigger failures deterministically.
- Bound dependency calls with `asyncio.wait_for`.
- Catch broad adapter exceptions only at a boundary that converts them to a stable
  public contract.
- Preserve the original non-zero exit from smoke procedures; cleanup warnings must
  not turn a failure into success.

## API Responses

M0 defines typed health responses only. New business errors must introduce an
explicit response model and contract tests in the milestone that owns them.

## Scenario: Database Pool Backpressure

### 1. Scope / Trigger

The API cannot acquire a SQLAlchemy database connection within the configured pool wait.

### 2. Signatures

`unexpected_error_response(error)` and `register_error_handlers(app)`
share the existing `ErrorResponse` boundary with authentication middleware.

### 3. Contracts

SQLAlchemy pool `TimeoutError` is a temporary capacity failure: return the existing
ErrorResponse shape with HTTP 503, `service_busy`, `Retry-After: 1` and the request ID.
Authentication middleware and route failures share the mapping. Register its typed
route handler inside ExceptionMiddleware so RequestContext is still available;
the outer catch-all runs after that context has been reset. Built-in TimeoutError
and unrelated exceptions retain the existing 500 contract. Never expose driver
messages or automatically replay a write because of this response; existing
idempotent/GET recovery determines whether a submitted operation was accepted.

### 4. Validation & Error Matrix

SQLAlchemy `TimeoutError` -> 503/service_busy; built-in `TimeoutError` and unrelated
programming errors -> existing 500/internal_error. Driver text never enters the response.

### 5. Good / Base / Bad Cases

Good: the client can wait one second and recover a safe read. Base: successful reads are
unchanged. Bad: classify every TimeoutError as capacity exhaustion or replay an unsafe write.

### 6. Tests Required

Exercise authentication and route boundaries with pool timeout, built-in timeout and
programming errors. Verify request ID, retry header, redaction and a later successful read.

### 7. Wrong vs Correct

Wrong: rely only on the outer catch-all after RequestContext cleanup. Correct: register
the typed pool handler inside ExceptionMiddleware and preserve correlation.

## Common Mistakes

Do not return raw exception strings, use HTTP 200 for `not_ready`, swallow process
startup failures, or use retries to convert a failing quality gate into success.

## Proven Examples

- `packages/core/src/enterprise_doc_core/health/models.py`
- `apps/api/src/enterprise_doc_api/app.py`
- `apps/worker/src/enterprise_doc_worker/app.py`
- `scripts/foundation_smoke.py`
- `apps/api/tests/test_database_backpressure.py`
