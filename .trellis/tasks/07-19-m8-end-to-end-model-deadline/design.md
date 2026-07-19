# M8 End-to-End Model Deadline: Design

## Boundary

`ChatModelGateway.generate(request)` remains the provider and Agent graph protocol.
`RoutedChatModelGateway` owns cross-provider scheduling, so it owns the route deadline.
This keeps deterministic, OpenAI-compatible, fault-injection and test gateways unchanged.

## Deadline Contract

At the start of each routed `generate` call, compute one absolute deadline from the
running event loop's monotonic clock. A private bounded-call helper computes remaining
time immediately before each provider call:

1. If no route deadline is configured, call the gateway directly.
2. If remaining time is non-positive, raise `ModelTimeoutError` without invoking it.
3. Otherwise use `asyncio.timeout_at(deadline)` and map only the timeout context's
   `TimeoutError` to `ModelTimeoutError`.
4. Do not catch or translate `CancelledError`.

The primary call goes through the existing error-classification branch. A route timeout
therefore records a retryable circuit failure. Fallback receives only the remaining
budget. Circuit-open traffic skips primary and can use the complete route budget.

## Configuration

`ModelSettings.route_deadline_seconds` is optional and bounded to 600 seconds. At the
Worker composition root:

- explicit configuration wins;
- otherwise a configured fallback uses `primary timeout + effective fallback timeout`;
- a route is not constructed when fallback is absent, preserving current single-provider
  behavior.

The sum default is compatibility-oriented. Production SLOs should set a tighter explicit
deadline after real latency measurements.

## Observability Semantics

`fallback_count` counts an actual fallback provider invocation, not merely a fallback
decision. It increments only after the helper confirms positive remaining budget.
Existing benchmark evidence remains local routing-contract evidence.

## Failure Semantics

- Permanent primary errors abort a half-open probe and never fallback.
- Retryable primary errors update the breaker, then fallback if budget remains.
- Route timeout is retryable and participates in breaker failure thresholds.
- A caller cancellation aborts a half-open probe and propagates unchanged.
- A fallback timeout propagates as `ModelTimeoutError`; no third route is attempted.

