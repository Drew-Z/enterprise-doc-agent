# M4 External Contract Decisions

Captured: 2026-07-18

## Research Method

Official library documentation and PyPI metadata were retrieved through the local
`smart-search` CLI. `smart-search doctor --format json` reported the broad compatible
chat provider at HTTP 429, while Context7 docs search and Tavily/Firecrawl fetch
capabilities remained available. No broad generated answer is used as evidence here.

Key commands:

```powershell
smart-search context7-library "langgraph python" "PostgresSaver interrupt Command resume checkpoint thread_id" --format json
smart-search context7-docs "/websites/langchain_oss_python_langgraph" "PostgresSaver interrupt Command resume" --format json
smart-search context7-library "model context protocol python sdk" "FastMCP stdio tools" --format json
smart-search context7-docs "/modelcontextprotocol/python-sdk" "stdio tools structured output" --format json
smart-search fetch "https://docs.langchain.com/oss/python/langgraph/interrupts" --format markdown
smart-search fetch "https://docs.langchain.com/oss/python/langgraph/persistence" --format markdown
smart-search fetch "https://github.com/langchain-ai/langgraph/tree/main/libs/checkpoint-postgres" --format markdown
smart-search fetch "https://github.com/modelcontextprotocol/python-sdk/blob/v1.x/README.md" --format markdown
smart-search fetch "https://pypi.org/pypi/langgraph/json" --format json
smart-search fetch "https://pypi.org/pypi/langgraph-checkpoint-postgres/json" --format json
smart-search fetch "https://pypi.org/pypi/mcp/json" --format json
```

## LangGraph Findings

Official sources:

- <https://docs.langchain.com/oss/python/langgraph/interrupts>
- <https://docs.langchain.com/oss/python/langgraph/persistence>
- <https://github.com/langchain-ai/langgraph/tree/main/libs/checkpoint-postgres>
- <https://pypi.org/project/langgraph/1.2.9/>
- <https://pypi.org/project/langgraph-checkpoint-postgres/3.1.0/>

Verified decisions:

1. Interrupts require a checkpointer and stable `thread_id`; resume uses the same
   thread with `Command(resume=...)`.
2. The node containing `interrupt()` restarts from the beginning when resumed. Code
   before the interrupt must be side-effect free or idempotent. M4 uses a pure interrupt
   node and performs approval-row creation in a prior idempotent node.
3. LangGraph checkpoints occur at graph step boundaries. External API/object/database
   operations still need explicit idempotency and cannot rely on the graph alone.
4. The official PostgreSQL saver requires `.setup()` for its tables. Manually supplied
   Psycopg connections require `autocommit=True` and `dict_row`.
5. The checkpoint package recommends strict msgpack/module allowlisting when database
   compromise is in scope. M4 requires `LANGGRAPH_STRICT_MSGPACK=true` and simple state.
6. PyPI reported stable LangGraph `1.2.9` and checkpoint-postgres `3.1.0`; M4 uses
   compatible major bounds rather than an unbounded latest dependency.

## MCP Findings

Official sources:

- <https://github.com/modelcontextprotocol/python-sdk/blob/v1.x/README.md>
- <https://github.com/modelcontextprotocol/python-sdk>
- <https://pypi.org/project/mcp/1.28.1/>
- <https://modelcontextprotocol.io/specification/latest>

Verified decisions:

1. The official Python SDK supports stdio and typed tool schemas/structured output.
2. A stdio server must reserve stdout for protocol messages; normal `print()` output
   can corrupt the transport. M4 sends operational logs to stderr.
3. Type annotations/Pydantic models generate tool schemas, but application-level
   authorization, idempotency, timeout, and approval remain server responsibilities.
4. As of capture, MCP Python `1.28.1` is the stable line. v2 is pre-release and its own
   README warns of breaking changes. M4 pins `mcp>=1.28.1,<2`.
5. MCP arguments are untrusted model/application input. Tenant and actor context must
   come from a trusted out-of-band server context, not tool parameters.

## Rejected Alternatives

- **Custom graph checkpoint table**: rejected because LangGraph has a maintained
  PostgreSQL saver and the milestone explicitly requires LangGraph checkpointing.
- **InMemorySaver for the completed workflow**: allowed only in narrow unit tests; it
  cannot satisfy process-restart acceptance.
- **Long-lived Job while waiting for approval**: rejected because it holds/repeatedly
  renews a Worker lease for human time. M4 uses initial/resume Job segments.
- **MCP v2 beta**: rejected for M4 because it is not the stable line at capture time.
- **Tenant/actor fields in model-visible tool schema**: rejected because they are
  forgeable. A signed per-run context and database reload are required.
- **Native browser EventSource with token query parameter**: rejected because the app
  uses bearer authentication and tokens must not enter URLs/logs. Use fetch streaming.
