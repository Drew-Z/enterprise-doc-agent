# MCP Backend Specification

The MCP backend is a local stdio boundary for the Agent workflow. It exposes only
the allowlisted tools documented in the Agent, MCP, and HITL contract; tool calls
must reload tenant membership, capability, execution context, document version,
approval, artifact, and publication state on the server side.

## Proven Examples

- `apps/mcp/src/enterprise_doc_mcp/`
- `apps/worker/src/enterprise_doc_worker/mcp_client.py`
- `tests/mcp/`, `tests/security/`, and `tests/contracts/`
