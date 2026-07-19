# M8 End-to-End Model Deadline

## Goal

Bound primary and fallback model execution with one monotonic route deadline while preserving cancellation, circuit-breaker, and public gateway contracts.

## Requirements

- **M8-R1**: A routed model request has one optional route-level deadline measured
  with a monotonic clock. Primary and fallback calls consume the same budget.
- **M8-R2**: A route deadline timeout is exposed as the stable retryable
  `ModelTimeoutError` and participates in existing circuit-breaker accounting.
- **M8-R3**: Fallback starts only when positive route budget remains. It may use no more
  than the remaining route budget, even when its provider timeout is longer.
- **M8-R4**: Caller cancellation remains `CancelledError`; it is never converted into a
  provider timeout, and a cancelled half-open probe cannot remain stuck in flight.
- **M8-R5**: The public `ChatModelGateway.generate(request)` protocol and Agent graph
  request contract remain unchanged.
- **M8-R6**: Configuration supports an explicit route deadline. When fallback is
  configured and the value is omitted, the compatibility default is the sum of the
  primary and effective fallback provider timeouts.
- **M8-R7**: Local tests and evidence prove routing-time contracts only. They do not
  claim provider SLOs, production latency, model quality, or capacity.

## Acceptance Criteria

- [x] Primary success, retryable fallback success, permanent error, circuit-open,
  exhausted-budget, half-open timeout, and caller-cancellation paths have tests.
- [x] Total primary-plus-fallback wall time is bounded by one route deadline.
- [x] An exhausted budget does not call the fallback gateway.
- [x] Timeout accounting opens or reopens the circuit according to existing policy.
- [x] Worker configuration passes the explicit or compatibility deadline to the routed
  gateway without changing single-provider behavior.
- [x] Ruff, mypy, focused tests, full backend tests, and evidence contracts pass.
- [x] Documentation no longer describes the shared deadline as missing.

## Notes

- The route deadline is an orchestration policy, not a field in the grounded business
  request or an argument added to every provider adapter.
- `deadline_seconds=None` preserves direct-construction compatibility for callers that
  intentionally do not enable a route-level bound.
- Provider-specific timeouts remain active; the effective limit is the smaller of the
  provider timeout and the route's remaining budget.
