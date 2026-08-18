# M7 Local Model Routing And Benchmark: Design

## Routing Contract

The Agent graph depends on `ChatModelGateway.generate`, not a concrete provider. A
`ProviderRoute` owns a primary and optional fallback gateway. Only retryable gateway
errors can trigger fallback. An exhausted bounded provider-output schema repair is a
retryable upstream generation failure; request/envelope contract, auth, authorization,
and grounding failures remain permanent. Primary and fallback telemetry is merged under
the shared route deadline, preserving an observed model identity over configured
descriptor metadata. A circuit breaker is per route identity, not per tenant or request,
and uses monotonic time with a single half-open probe.

For prompt v8/v9, the gateway may repair a citation's identifier pair without another
provider request only when the stripped excerpt is a verbatim substring of exactly one
authorized evidence item. The gateway replaces both identifiers with that item's exact
pair and marks the output repaired. Zero or multiple matches remain unchanged so the
deterministic grounding gate rejects them.

## Identity And Evidence

Every result records provider/model/revision/quantization/context limit, graph/prompt/tool
versions, dataset hash, latency samples, fallback count and citation/authorization outcome.
Secrets, prompts and raw outputs are excluded. Deterministic local runs prove routing
and state transitions; real vLLM/GPU benchmark evidence is a separate manual gate.
