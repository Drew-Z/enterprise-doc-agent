# M7 Evidence Boundary

The two JSON reports in this directory are local deterministic routing and fallback
contract runs. They prove schema validation, citation preservation, and breaker/fallback
behavior for the checked-in gateway. They do not prove real model quality, provider
availability, GPU/vLLM throughput, quantization memory, cost, or production capacity.

The manifest is intentionally `working-tree`: the implementation and evidence have not
been reviewed or committed as immutable artifacts. Real-provider and hardware gates stay
`blocked_external` until a dedicated environment produces sanitized reports and hashes.
