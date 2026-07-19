# M8 End-to-End Model Deadline: TDD Implementation Plan

## Slice 0: Contract Tests

- [x] Add deterministic tests for shared remaining budget, exhausted fallback budget,
  circuit-open fallback budget, half-open timeout and caller cancellation.
- [x] Add configuration validation and assembly tests.

## Slice 1: Router Implementation

- [x] Add optional route deadline and monotonic bounded-call helper.
- [x] Preserve stable error classes, circuit transitions and cancellation semantics.
- [x] Count only actual fallback provider invocations.

## Slice 2: Configuration And Documentation

- [x] Add `route_deadline_seconds` to `ModelSettings` and `.env.example`.
- [x] Apply explicit or compatibility deadline in the Worker composition root.
- [x] Update model-routing spec, README and interview documentation.

## Slice 3: Verification And Evidence

- [x] Run focused tests, full backend suite, Ruff and mypy.
- [x] Record a new immutable M8 local evidence manifest and update the evidence index.
- [x] Keep real-provider latency/SLO and production-capacity evidence external.
