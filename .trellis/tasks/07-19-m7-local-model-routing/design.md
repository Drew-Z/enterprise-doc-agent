# M7 Local Model Routing And Benchmark: Design

## Routing Contract

The Agent graph depends on `ChatModelGateway.generate`, not a concrete provider. A
`ProviderRoute` owns a primary and optional fallback gateway. Only retryable gateway
errors can trigger fallback. A circuit breaker is per route identity, not per tenant or
request, and uses monotonic time with a single half-open probe.

## Identity And Evidence

Every result records provider/model/revision/quantization/context limit, graph/prompt/tool
versions, dataset hash, latency samples, fallback count and citation/authorization outcome.
Secrets, prompts and raw outputs are excluded. Deterministic local runs prove routing
and state transitions; real vLLM/GPU benchmark evidence is a separate manual gate.
