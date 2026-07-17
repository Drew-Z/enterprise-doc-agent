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

## Common Mistakes

Do not return raw exception strings, use HTTP 200 for `not_ready`, swallow process
startup failures, or use retries to convert a failing quality gate into success.

## Proven Examples

- `packages/core/src/enterprise_doc_core/health/models.py`
- `apps/api/src/enterprise_doc_api/app.py`
- `apps/worker/src/enterprise_doc_worker/app.py`
- `scripts/foundation_smoke.py`
