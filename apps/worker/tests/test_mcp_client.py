from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from mcp.types import CallToolResult, TextContent

from enterprise_doc_core.agents import SearchDocumentInput, SearchDocumentResult
from enterprise_doc_core.telemetry import MetricsRuntime
from enterprise_doc_worker.mcp_client import (
    CONTEXT_ENV,
    InstrumentedMcpClient,
    McpClientTimeout,
    McpClientTransportError,
    McpStdioClient,
    McpToolResultInvalid,
    McpToolRetryableError,
    McpToolReturnedError,
)

RUN_ID = uuid4()


def _result() -> dict[str, Any]:
    return {
        "execution_id": str(uuid4()),
        "replayed": False,
        "accepted": False,
        "refusal_reason": "empty_evidence",
        "candidates": [],
    }


class FakeTransport:
    def __init__(
        self,
        params: Any,
        *,
        streams: tuple[Any, Any] = ("r", "w"),
        exit_error: BaseException | None = None,
    ) -> None:
        self.params = params
        self.streams = streams
        self.exit_error = exit_error
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> tuple[Any, Any]:
        self.entered = True
        return self.streams

    async def __aexit__(self, *_: object) -> None:
        self.exited = True
        if self.exit_error is not None:
            raise self.exit_error


class FakeSession:
    def __init__(
        self,
        result: Any = None,
        *,
        initialize_error: BaseException | None = None,
    ) -> None:
        self.result = result
        self.initialize_error = initialize_error
        self.entered = False
        self.exited = False
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    async def __aenter__(self) -> FakeSession:
        self.entered = True
        return self

    async def __aexit__(self, *_: object) -> None:
        self.exited = True

    async def initialize(self) -> None:
        if self.initialize_error is not None:
            raise self.initialize_error

    async def call_tool(self, name: str, *, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))
        return self.result


def _client(transport: FakeTransport, session: FakeSession) -> McpStdioClient:
    return McpStdioClient(
        command="fake-mcp",
        args=("--stdio",),
        request_timeout_seconds=0.05,
        transport_factory=lambda _: transport,
        session_factory=lambda _read, _write: session,
    )


@pytest.mark.asyncio
async def test_mcp_client_injects_context_only_in_environment_and_parses_structured_result() -> (
    None
):
    result = CallToolResult(content=[], structuredContent=_result())
    transport = FakeTransport(None)
    session = FakeSession(result)
    transport.params = None

    def make_transport(params: Any) -> FakeTransport:
        transport.params = params
        return transport

    client = McpStdioClient(
        command="fake-mcp",
        args=("--stdio",),
        request_timeout_seconds=1,
        transport_factory=make_transport,
        session_factory=lambda _read, _write: session,
    )
    request = SearchDocumentInput(idempotency_key="search-1", query="payment")

    parsed = await client.search_document(context_token="secret-context", request=request)

    assert isinstance(parsed, SearchDocumentResult)
    assert transport.params.env[CONTEXT_ENV] == "secret-context"
    assert "secret-context" not in transport.params.args
    assert session.calls == [("search_document", {"request": request.model_dump(mode="json")})]
    assert transport.entered and transport.exited
    assert session.entered and session.exited


@pytest.mark.asyncio
async def test_mcp_client_rejects_error_and_missing_structured_content() -> None:
    request = SearchDocumentInput(idempotency_key="search-1", query="payment")
    for result, expected in (
        (CallToolResult(content=[], isError=True), McpToolReturnedError),
        (CallToolResult(content=[]), McpToolResultInvalid),
    ):
        transport = FakeTransport(None)
        session = FakeSession(result)
        client = _client(transport, session)

        with pytest.raises(expected):
            await client.search_document(context_token="secret-context", request=request)

        assert transport.exited and session.exited


@pytest.mark.asyncio
async def test_mcp_client_preserves_tool_error_wrapped_during_transport_cleanup() -> None:
    request = SearchDocumentInput(idempotency_key="search-1", query="payment")
    wrapped = ExceptionGroup(
        "stdio cleanup",
        [
            ExceptionGroup("tool call", [McpToolReturnedError()]),
            RuntimeError("process cleanup failed"),
        ],
    )
    transport = FakeTransport(None, exit_error=wrapped)
    session = FakeSession(CallToolResult(content=[], isError=True))
    client = _client(transport, session)

    with pytest.raises(McpToolReturnedError):
        await client.search_document(context_token="secret-context", request=request)

    assert transport.exited and session.exited


@pytest.mark.asyncio
async def test_mcp_client_classifies_grouped_timeout_and_transport_errors() -> None:
    request = SearchDocumentInput(idempotency_key="search-1", query="payment")
    result = CallToolResult(content=[], structuredContent=_result())
    for grouped_error, expected in (
        (ExceptionGroup("timeout", [TimeoutError()]), McpClientTimeout),
        (ExceptionGroup("transport", [RuntimeError("broken pipe")]), McpClientTransportError),
    ):
        transport = FakeTransport(None, exit_error=grouped_error)
        client = _client(transport, FakeSession(result))

        with pytest.raises(expected):
            await client.search_document(context_token="secret-context", request=request)


@pytest.mark.asyncio
async def test_mcp_client_propagates_grouped_cancellation() -> None:
    request = SearchDocumentInput(idempotency_key="search-1", query="payment")
    grouped_error = BaseExceptionGroup("cancelled", [asyncio.CancelledError()])
    transport = FakeTransport(None, exit_error=grouped_error)
    session = FakeSession(CallToolResult(content=[], structuredContent=_result()))
    client = _client(transport, session)

    with pytest.raises(asyncio.CancelledError):
        await client.search_document(context_token="secret-context", request=request)


@pytest.mark.asyncio
async def test_mcp_client_timeout_still_closes_transport_and_session() -> None:
    request = SearchDocumentInput(idempotency_key="search-1", query="payment")

    class SlowSession(FakeSession):
        async def call_tool(self, name: str, *, arguments: dict[str, Any]) -> Any:
            await asyncio.sleep(1)
            return None

    transport = FakeTransport(None)
    session = SlowSession()
    client = _client(transport, session)

    with pytest.raises(McpClientTimeout):
        await client.search_document(context_token="secret-context", request=request)

    assert transport.exited and session.exited


@pytest.mark.asyncio
async def test_mcp_client_propagates_cancellation_and_closes_resources() -> None:
    request = SearchDocumentInput(idempotency_key="search-1", query="payment")

    class BlockingSession(FakeSession):
        async def call_tool(self, name: str, *, arguments: dict[str, Any]) -> Any:
            await asyncio.Event().wait()
            return None

    transport = FakeTransport(None)
    session = BlockingSession()
    client = _client(transport, session)
    task = asyncio.create_task(
        client.search_document(context_token="secret-context", request=request)
    )
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert transport.exited and session.exited


@pytest.mark.asyncio
async def test_mcp_client_preserves_known_retryable_tool_errors() -> None:
    request = SearchDocumentInput(idempotency_key="search-1", query="payment")
    session = FakeSession(
        CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=("Error executing tool search_document: tool_object_store_unavailable"),
                )
            ],
            isError=True,
        )
    )
    client = _client(FakeTransport(None), session)

    with pytest.raises(McpToolRetryableError) as exc_info:
        await client.search_document(context_token="secret-context", request=request)

    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_mcp_client_finds_retryable_codes_across_protocol_error_shapes() -> None:
    request = SearchDocumentInput(idempotency_key="search-1", query="payment")
    results = (
        SimpleNamespace(
            isError=True,
            content=(
                TextContent(
                    type="text",
                    text="Error executing tool search_document: tool_execution_in_progress",
                ),
            ),
        ),
        SimpleNamespace(
            isError=True,
            content=(),
            structuredContent={"error": {"code": "tool_execution_in_progress"}},
        ),
    )

    for result in results:
        client = _client(FakeTransport(None), FakeSession(result))

        with pytest.raises(McpToolRetryableError) as exc_info:
            await client.search_document(context_token="secret-context", request=request)

        assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_instrumented_mcp_client_records_success_and_timeout() -> None:
    request = SearchDocumentInput(idempotency_key="search-1", query="payment")
    metrics = MetricsRuntime.create()
    success = InstrumentedMcpClient(
        _client(
            FakeTransport(None),
            FakeSession(CallToolResult(content=[], structuredContent=_result())),
        ),
        metrics,
    )

    result = await success.search_document(context_token="secret-context", request=request)
    assert isinstance(result, SearchDocumentResult)

    class SlowSession(FakeSession):
        async def call_tool(self, name: str, *, arguments: dict[str, Any]) -> Any:
            await asyncio.sleep(1)
            return None

    timeout = InstrumentedMcpClient(
        _client(FakeTransport(None), SlowSession()),
        metrics,
    )
    with pytest.raises(McpClientTimeout):
        await timeout.search_document(context_token="secret-context", request=request)

    rendered = metrics.render().decode("utf-8")
    assert 'boundary="mcp",operation="call",result="success"' in rendered
    assert 'boundary="mcp",operation="call",result="timeout"' in rendered
