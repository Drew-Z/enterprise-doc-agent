from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from enterprise_doc_core.agents import (
    AgentToolService,
    SearchDocumentResult,
    SignedExecutionContext,
    ToolCapability,
    ToolObjectStoreUnavailable,
    sign_execution_context,
)
from enterprise_doc_core.config import FoundationSettings
from enterprise_doc_core.context import (
    PrincipalContext,
    RequestContext,
    get_request_context,
    reset_request_context,
    set_request_context,
)
from enterprise_doc_core.logging import configure_logging
from enterprise_doc_core.telemetry import MetricsRuntime
from enterprise_doc_mcp.server import (
    McpRuntime,
    McpServerError,
    build_runtime,
    build_server,
)

SECRET = "m" * 40
NOW = datetime(2026, 7, 19, 10, 0, tzinfo=UTC)


class FakeToolService:
    def __init__(self) -> None:
        self.search_calls = 0

    async def search_document(self, context: object, request: object) -> SearchDocumentResult:
        assert context is not None
        assert request is not None
        self.search_calls += 1
        return SearchDocumentResult(
            execution_id=UUID("00000000-0000-0000-0000-000000000004"),
            replayed=False,
            accepted=False,
            refusal_reason="empty_evidence",
            candidates=(),
        )


def _token() -> str:
    context = SignedExecutionContext(
        tenant_id=UUID("00000000-0000-0000-0000-000000000001"),
        actor_id=UUID("00000000-0000-0000-0000-000000000002"),
        run_id=UUID("00000000-0000-0000-0000-000000000003"),
        execution_id=UUID("00000000-0000-0000-0000-000000000004"),
        capabilities=(ToolCapability.READ_EVIDENCE,),
        target_document_version_id=UUID("00000000-0000-0000-0000-000000000005"),
        issued_at=NOW - timedelta(seconds=1),
        expires_at=NOW + timedelta(minutes=5),
        nonce="nonce_1234567890",
    )
    return sign_execution_context(context, SECRET)


async def test_server_registers_exactly_five_strict_tools_without_context_fields() -> None:
    runtime = McpRuntime(
        service=cast(AgentToolService, FakeToolService()),
        signing_secret=SECRET,
        context_token=_token(),
        clock=lambda: NOW,
    )
    tools = await build_server(runtime).list_tools()

    assert {tool.name for tool in tools} == {
        "search_document",
        "read_chunk",
        "create_draft_artifact",
        "get_artifact",
        "publish_artifact",
    }
    for tool in tools:
        encoded = str(tool.inputSchema)
        assert "tenant_id" not in encoded
        assert "actor_id" not in encoded
        assert "run_id" not in encoded
        assert "approval_request_id" not in encoded
        assert tool.inputSchema["additionalProperties"] is False
        assert all(
            definition.get("additionalProperties") is False
            for definition in tool.inputSchema.get("$defs", {}).values()
            if isinstance(definition, dict) and "properties" in definition
        )


async def test_server_runtime_rejects_unknown_top_level_arguments() -> None:
    service = FakeToolService()
    runtime = McpRuntime(
        service=cast(AgentToolService, service),
        signing_secret=SECRET,
        context_token=_token(),
        clock=lambda: NOW,
    )

    with pytest.raises(ToolError, match="Extra inputs are not permitted"):
        await build_server(runtime).call_tool(
            "search_document",
            {
                "request": {"idempotency_key": "search-extra", "query": "payment"},
                "unexpected": True,
            },
        )

    assert service.search_calls == 0


async def test_server_tool_argument_models_reject_extra_fields_for_every_tool() -> None:
    runtime = McpRuntime(
        service=cast(AgentToolService, FakeToolService()),
        signing_secret=SECRET,
        context_token=_token(),
        clock=lambda: NOW,
    )
    server = build_server(runtime)
    tools = await server.list_tools()

    for tool in tools:
        arg_model = server._tool_manager._tools[tool.name].fn_metadata.arg_model
        with pytest.raises(ValueError) as error:
            arg_model.model_validate({"unexpected": True})
        assert any(item["type"] == "extra_forbidden" for item in error.value.errors())


async def test_server_uses_signed_out_of_band_context_for_tool_calls() -> None:
    service = FakeToolService()
    runtime = McpRuntime(
        service=cast(AgentToolService, service),
        signing_secret=SECRET,
        context_token=_token(),
        clock=lambda: NOW,
    )
    _, structured = await build_server(runtime).call_tool(
        "search_document",
        {"request": {"idempotency_key": "search-1", "query": "payment"}},
    )

    assert isinstance(structured, dict)
    assert structured["accepted"] is False
    assert structured["refusal_reason"] == "empty_evidence"


async def test_server_records_tool_boundary_and_runtime_shares_metrics_registry() -> None:
    metrics = MetricsRuntime.create()
    runtime = McpRuntime(
        service=cast(AgentToolService, FakeToolService()),
        signing_secret=SECRET,
        context_token=_token(),
        clock=lambda: NOW,
        metrics=metrics,
    )
    _, structured = await build_server(runtime).call_tool(
        "search_document",
        {"request": {"idempotency_key": "search-metrics", "query": "payment"}},
    )
    assert isinstance(structured, dict)
    rendered = metrics.render().decode("utf-8")
    assert 'boundary="mcp",operation="server_call",result="success"' in rendered

    resources = build_runtime(FoundationSettings(_env_file=None), metrics=metrics)
    try:
        assert resources.metrics is metrics
        assert resources.artifact_store.metrics is metrics
        assert resources.runtime.service.retrieval_service.metrics is metrics
    finally:
        await resources.close()


async def test_server_records_retryable_tool_failures() -> None:
    class UnavailableToolService(FakeToolService):
        async def search_document(
            self,
            context: object,
            request: object,
        ) -> SearchDocumentResult:
            raise ToolObjectStoreUnavailable()

    metrics = MetricsRuntime.create()
    runtime = McpRuntime(
        service=cast(AgentToolService, UnavailableToolService()),
        signing_secret=SECRET,
        context_token=_token(),
        clock=lambda: NOW,
        metrics=metrics,
    )

    with pytest.raises(ToolError):
        await build_server(runtime).call_tool(
            "search_document",
            {"request": {"idempotency_key": "search-retry", "query": "payment"}},
        )

    rendered = metrics.render().decode("utf-8")
    assert 'boundary="mcp",operation="server_call",result="retryable_error"' in rendered


async def test_server_resets_request_context_after_tool_call() -> None:
    service = FakeToolService()
    runtime = McpRuntime(
        service=cast(AgentToolService, service),
        signing_secret=SECRET,
        context_token=_token(),
        clock=lambda: NOW,
    )
    previous = RequestContext(
        request_id="previous-request",
        correlation_id="previous-correlation",
        principal=PrincipalContext(tenant_id="tenant", actor_id="actor", role="test"),
    )
    token = set_request_context(previous)
    try:
        await build_server(runtime).call_tool(
            "search_document",
            {"request": {"idempotency_key": "search-context", "query": "payment"}},
        )
        assert get_request_context() == previous
    finally:
        reset_request_context(token)


def test_runtime_rejects_missing_or_tampered_context_without_echoing_token() -> None:
    runtime = McpRuntime(
        service=cast(AgentToolService, FakeToolService()),
        signing_secret=SECRET,
        context_token=None,
    )
    with pytest.raises(McpServerError) as missing:
        runtime.load_context()
    assert missing.value.code == "execution_context_missing"

    token = _token()
    runtime.context_token = token[:-1] + "A"
    with pytest.raises(McpServerError) as tampered:
        runtime.load_context()
    assert tampered.value.code == "execution_context_invalid_signature"
    assert token not in str(tampered.value)


def test_server_argument_models_accept_canonical_json_uuid_strings() -> None:
    runtime = McpRuntime(
        service=cast(AgentToolService, FakeToolService()),
        signing_secret=SECRET,
        context_token=_token(),
        clock=lambda: NOW,
    )
    server = build_server(runtime)
    resource_id = UUID("00000000-0000-0000-0000-000000000006")

    for tool_name, field_name in (
        ("read_chunk", "chunk_id"),
        ("get_artifact", "artifact_id"),
        ("publish_artifact", "artifact_id"),
    ):
        request = {
            "idempotency_key": f"{tool_name}-wire-uuid",
            field_name: str(resource_id),
        }
        if tool_name == "publish_artifact":
            request["target_fingerprint"] = "a" * 64
        arg_model = server._tool_manager._tools[tool_name].fn_metadata.arg_model
        parsed = arg_model.model_validate({"request": request})
        assert getattr(parsed.request, field_name) == resource_id

    draft_model = server._tool_manager._tools["create_draft_artifact"].fn_metadata.arg_model
    parsed_draft = draft_model.model_validate(
        {
            "request": {
                "idempotency_key": "create-draft-wire-uuid",
                "answer_text": "The evidence retention period is thirty days.",
                "citations": [
                    {
                        "chunk_id": str(resource_id),
                        "document_version_id": str(resource_id),
                        "excerpt": "evidence retention period is thirty days",
                    }
                ],
            }
        }
    )
    assert parsed_draft.request.citations[0].chunk_id == resource_id
    assert parsed_draft.request.citations[0].document_version_id == resource_id


def test_mcp_json_logging_writes_to_stderr_only(
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = logging.getLogger()
    previous_handlers = list(root.handlers)
    previous_level = root.level
    try:
        configure_logging(service="enterprise-doc-mcp", environment="test", level="INFO")
        logging.getLogger("enterprise_doc_mcp.test").info("mcp_stdio_log_probe")
        captured = capsys.readouterr()
    finally:
        root.handlers = previous_handlers
        root.setLevel(previous_level)

    assert captured.out == ""
    payload = json.loads(captured.err)
    assert payload["service"] == "enterprise-doc-mcp"
    assert payload["event"] == "mcp_stdio_log_probe"
