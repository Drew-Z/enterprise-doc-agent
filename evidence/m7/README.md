# M7 Evidence Boundary

The two JSON reports in this directory are local deterministic routing and fallback
contract runs. They prove schema validation, citation preservation, and breaker/fallback
behavior for the checked-in gateway. They do not prove real model quality, provider
availability, GPU/vLLM throughput, quantization memory, cost, or production capacity.

The formal manifest points to the reviewed implementation commit, the sanitized local
evidence commit, and the repository status snapshot that first published the manifest.
These local artifacts are immutable, but the milestone remains `blocked_external` until
a dedicated environment produces sanitized real-provider and GPU/vLLM capacity reports.
