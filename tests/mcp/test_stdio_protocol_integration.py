from __future__ import annotations

import os
import sys

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


@pytest.mark.integration
async def test_mcp_stdio_stdout_is_protocol_only_and_lists_v1_tools() -> None:
    environment = os.environ.copy()
    environment["MCP__SIGNING_SECRET"] = "s" * 40
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "enterprise_doc_mcp"],
        cwd=os.getcwd(),
        env=environment,
    )
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
    assert {tool.name for tool in tools.tools} == {
        "search_document",
        "read_chunk",
        "create_draft_artifact",
        "get_artifact",
        "publish_artifact",
    }
