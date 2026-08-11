from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol, TypeVar

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import BaseModel, SecretStr, ValidationError

from enterprise_doc_core.agents import (
    CreateDraftArtifactInput,
    CreateDraftArtifactResult,
    GetArtifactInput,
    GetArtifactResult,
    PublishArtifactInput,
    PublishArtifactResult,
    ReadChunkInput,
    ReadChunkResult,
    SearchDocumentInput,
    SearchDocumentResult,
)
from enterprise_doc_core.jobs import MCP_DIAGNOSTIC_SUBCODES, mcp_diagnostic_code
from enterprise_doc_core.telemetry import MetricsRuntime
from enterprise_doc_worker.queue import JobHandlerError

CONTEXT_ENV = "ENTERPRISE_DOC_MCP_CONTEXT"
_ResultT = TypeVar("_ResultT", bound=BaseModel)
_RETRYABLE_MCP_SUBCODES = frozenset(
    {
        "mcp_tool_timeout",
        "tool_execution_in_progress",
        "tool_object_store_unavailable",
    }
)


class McpClientError(JobHandlerError):
    code = "mcp_client_error"
    message = "The MCP tool call could not be completed."


class McpClientTimeout(McpClientError):
    code = "mcp_client_timeout"
    message = "The MCP tool call timed out."
    retryable = True


class McpClientTransportError(McpClientError):
    code = "mcp_client_transport_error"
    message = "The MCP tool process was unavailable."
    retryable = True


class McpToolReturnedError(McpClientError):
    code = "mcp_tool_returned_error"
    message = "The MCP tool rejected the request."


class McpToolRetryableError(McpToolReturnedError):
    code = "mcp_tool_retryable_error"
    message = "The MCP tool reported a retryable failure."
    retryable = True


class McpToolResultInvalid(McpClientError):
    code = "mcp_tool_result_invalid"
    message = "The MCP tool returned an invalid structured result."


TransportFactory = Callable[
    [StdioServerParameters],
    AbstractAsyncContextManager[tuple[Any, Any]],
]
SessionFactory = Callable[[Any, Any], AbstractAsyncContextManager[Any]]


class McpClient(Protocol):
    async def call[ResultT: BaseModel](
        self,
        *,
        tool_name: str,
        request: BaseModel,
        result_model: type[ResultT],
        context_token: SecretStr | str,
    ) -> ResultT: ...

    async def search_document(
        self, *, context_token: SecretStr | str, request: SearchDocumentInput
    ) -> SearchDocumentResult: ...

    async def read_chunk(
        self, *, context_token: SecretStr | str, request: ReadChunkInput
    ) -> ReadChunkResult: ...

    async def create_draft_artifact(
        self, *, context_token: SecretStr | str, request: CreateDraftArtifactInput
    ) -> CreateDraftArtifactResult: ...

    async def get_artifact(
        self, *, context_token: SecretStr | str, request: GetArtifactInput
    ) -> GetArtifactResult: ...

    async def publish_artifact(
        self, *, context_token: SecretStr | str, request: PublishArtifactInput
    ) -> PublishArtifactResult: ...


class McpStdioClient:
    """Small, strict Worker-side adapter for the versioned MCP stdio server."""

    def __init__(
        self,
        *,
        command: str = "enterprise-doc-mcp",
        args: Sequence[str] = (),
        cwd: str | Path | None = None,
        request_timeout_seconds: float = 30.0,
        environment: dict[str, str] | None = None,
        transport_factory: TransportFactory | None = None,
        session_factory: SessionFactory | None = None,
    ) -> None:
        if not command.strip():
            raise ValueError("MCP command must not be blank")
        if request_timeout_seconds <= 0:
            raise ValueError("MCP request timeout must be positive")
        self.command = command
        self.args = tuple(args)
        self.cwd = cwd
        self.request_timeout_seconds = request_timeout_seconds
        self.environment = dict(environment or {})
        self._transport_factory = transport_factory or stdio_client
        self._session_factory = session_factory or (
            lambda read_stream, write_stream: ClientSession(read_stream, write_stream)
        )

    async def call(
        self,
        *,
        tool_name: str,
        request: BaseModel,
        result_model: type[_ResultT],
        context_token: SecretStr | str,
    ) -> _ResultT:
        token = _secret_value(context_token)
        if not token:
            raise McpClientError("The MCP execution context is missing.")
        environment = os.environ.copy()
        environment.update(self.environment)
        environment[CONTEXT_ENV] = token
        parameters = StdioServerParameters(
            command=self.command,
            args=list(self.args),
            env=environment,
            cwd=self.cwd,
        )
        arguments = {"request": request.model_dump(mode="json")}

        try:
            async with asyncio.timeout(self.request_timeout_seconds):
                async with self._transport_factory(parameters) as streams:
                    read_stream, write_stream = streams
                    async with self._session_factory(read_stream, write_stream) as session:
                        async with asyncio.timeout(self.request_timeout_seconds):
                            await session.initialize()
                        async with asyncio.timeout(self.request_timeout_seconds):
                            result = await session.call_tool(tool_name, arguments=arguments)
                        return _parse_result(result, result_model, tool_name=tool_name)
        except asyncio.CancelledError:
            raise
        except McpClientError:
            raise
        except BaseExceptionGroup as error:
            leaves = _exception_leaves(error)
            if any(
                not isinstance(leaf, Exception) and not isinstance(leaf, asyncio.CancelledError)
                for leaf in leaves
            ):
                raise

            client_errors = [leaf for leaf in leaves if isinstance(leaf, McpClientError)]
            if client_errors and all(
                type(candidate) is type(client_errors[0])
                and str(candidate) == str(client_errors[0])
                for candidate in client_errors
            ):
                # AnyIO may combine the original tool error with transport cleanup
                # failures. Keep the stable business classification and retain the
                # full group as its cause for diagnostics.
                raise client_errors[0] from error
            if any(isinstance(leaf, TimeoutError) for leaf in leaves):
                raise McpClientTimeout() from error
            cancellation = next(
                (leaf for leaf in leaves if isinstance(leaf, asyncio.CancelledError)),
                None,
            )
            if cancellation is not None:
                raise cancellation from error
            raise McpClientTransportError() from error
        except TimeoutError as error:
            raise McpClientTimeout() from error
        except Exception as error:
            raise McpClientTransportError() from error

    async def search_document(
        self, *, context_token: SecretStr | str, request: SearchDocumentInput
    ) -> SearchDocumentResult:
        return await self.call(
            tool_name="search_document",
            request=request,
            result_model=SearchDocumentResult,
            context_token=context_token,
        )

    async def read_chunk(
        self, *, context_token: SecretStr | str, request: ReadChunkInput
    ) -> ReadChunkResult:
        return await self.call(
            tool_name="read_chunk",
            request=request,
            result_model=ReadChunkResult,
            context_token=context_token,
        )

    async def create_draft_artifact(
        self, *, context_token: SecretStr | str, request: CreateDraftArtifactInput
    ) -> CreateDraftArtifactResult:
        return await self.call(
            tool_name="create_draft_artifact",
            request=request,
            result_model=CreateDraftArtifactResult,
            context_token=context_token,
        )

    async def get_artifact(
        self, *, context_token: SecretStr | str, request: GetArtifactInput
    ) -> GetArtifactResult:
        return await self.call(
            tool_name="get_artifact",
            request=request,
            result_model=GetArtifactResult,
            context_token=context_token,
        )

    async def publish_artifact(
        self, *, context_token: SecretStr | str, request: PublishArtifactInput
    ) -> PublishArtifactResult:
        return await self.call(
            tool_name="publish_artifact",
            request=request,
            result_model=PublishArtifactResult,
            context_token=context_token,
        )


class InstrumentedMcpClient:
    """Observe MCP calls with bounded labels after fault decoration."""

    def __init__(self, inner: McpClient, metrics: MetricsRuntime) -> None:
        self.inner = inner
        self.metrics = metrics

    async def call[ResultT: BaseModel](
        self,
        *,
        tool_name: str,
        request: BaseModel,
        result_model: type[ResultT],
        context_token: SecretStr | str,
    ) -> ResultT:
        started = perf_counter()
        result_label = "error"
        try:
            result = await self.inner.call(
                tool_name=tool_name,
                request=request,
                result_model=result_model,
                context_token=context_token,
            )
        except asyncio.CancelledError:
            result_label = "cancelled"
            raise
        except McpClientTimeout:
            result_label = "timeout"
            raise
        except McpClientError as error:
            result_label = "retryable_error" if error.retryable else "permanent_error"
            raise
        except Exception:
            result_label = "error"
            raise
        else:
            result_label = "success"
            return result
        finally:
            self.metrics.observe_boundary(
                boundary="mcp",
                operation="call",
                result=result_label,
                duration=perf_counter() - started,
            )

    async def search_document(
        self, *, context_token: SecretStr | str, request: SearchDocumentInput
    ) -> SearchDocumentResult:
        return await self.call(
            tool_name="search_document",
            request=request,
            result_model=SearchDocumentResult,
            context_token=context_token,
        )

    async def read_chunk(
        self, *, context_token: SecretStr | str, request: ReadChunkInput
    ) -> ReadChunkResult:
        return await self.call(
            tool_name="read_chunk",
            request=request,
            result_model=ReadChunkResult,
            context_token=context_token,
        )

    async def create_draft_artifact(
        self, *, context_token: SecretStr | str, request: CreateDraftArtifactInput
    ) -> CreateDraftArtifactResult:
        return await self.call(
            tool_name="create_draft_artifact",
            request=request,
            result_model=CreateDraftArtifactResult,
            context_token=context_token,
        )

    async def get_artifact(
        self, *, context_token: SecretStr | str, request: GetArtifactInput
    ) -> GetArtifactResult:
        return await self.call(
            tool_name="get_artifact",
            request=request,
            result_model=GetArtifactResult,
            context_token=context_token,
        )

    async def publish_artifact(
        self, *, context_token: SecretStr | str, request: PublishArtifactInput
    ) -> PublishArtifactResult:
        return await self.call(
            tool_name="publish_artifact",
            request=request,
            result_model=PublishArtifactResult,
            context_token=context_token,
        )


def _secret_value(value: SecretStr | str) -> str:
    return value.get_secret_value() if isinstance(value, SecretStr) else value


def _exception_leaves(error: BaseException) -> tuple[BaseException, ...]:
    if isinstance(error, BaseExceptionGroup):
        return tuple(leaf for nested in error.exceptions for leaf in _exception_leaves(nested))
    return (error,)


def _parse_result[ResultT: BaseModel](
    result: Any,
    result_model: type[ResultT],
    *,
    tool_name: str,
) -> ResultT:
    if bool(getattr(result, "isError", getattr(result, "is_error", False))):
        structured_payloads = (
            getattr(result, "structuredContent", None),
            getattr(result, "structured_content", None),
        )
        subcode = _matched_error_subcode(
            content=getattr(result, "content", ()),
            structured_payloads=structured_payloads,
            tool_name=tool_name,
        )
        diagnostic_code = mcp_diagnostic_code(tool_name=tool_name, subcode=subcode)
        if subcode in _RETRYABLE_MCP_SUBCODES:
            raise McpToolRetryableError(diagnostic_code=diagnostic_code)
        raise McpToolReturnedError(diagnostic_code=diagnostic_code)
    structured = getattr(result, "structuredContent", None)
    if not isinstance(structured, dict):
        raise McpToolResultInvalid()
    try:
        # JSON validation keeps UUID/datetime wire representations valid while
        # rejecting coercions such as a string where a JSON integer is required.
        encoded = json.dumps(structured, ensure_ascii=True, separators=(",", ":"))
        return result_model.model_validate_json(encoded, strict=True)
    except (TypeError, ValueError, ValidationError) as error:
        raise McpToolResultInvalid() from error


def _allowlisted_subcode(value: object) -> str | None:
    if not isinstance(value, str) or value == "returned_error":
        return None
    return value if value in MCP_DIAGNOSTIC_SUBCODES else None


def _iter_content_texts(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, BaseModel):
        return _iter_content_texts(value.model_dump(mode="json", by_alias=True))
    if isinstance(value, Mapping):
        text = value.get("text")
        return (text,) if isinstance(text, str) else ()
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return tuple(text for item in value for text in _iter_content_texts(item))
    text = getattr(value, "text", None)
    return (text,) if isinstance(text, str) else ()


def _subcode_from_content_text(text: str, *, tool_name: str) -> str | None:
    normalized = text.strip()
    direct = _allowlisted_subcode(normalized)
    if direct is not None:
        return direct
    prefixes = (
        f"Error executing tool {tool_name}:",
        f"Error executing tool '{tool_name}':",
        f'Error executing tool "{tool_name}":',
    )
    for prefix in prefixes:
        if normalized.startswith(prefix):
            return _allowlisted_subcode(normalized[len(prefix) :].strip())
    return None


def _iter_structured_error_codes(value: Any) -> tuple[str, ...]:
    if isinstance(value, BaseModel):
        return _iter_structured_error_codes(value.model_dump(mode="json", by_alias=True))
    if isinstance(value, Mapping):
        codes: list[str] = []
        for key, item in value.items():
            if key in {"code", "errorCode", "error_code"}:
                code = _allowlisted_subcode(item)
                if code is not None:
                    codes.append(code)
            elif isinstance(item, (BaseModel, Mapping, Sequence)) and not isinstance(
                item, (str, bytes, bytearray)
            ):
                codes.extend(_iter_structured_error_codes(item))
        return tuple(codes)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(code for item in value for code in _iter_structured_error_codes(item))
    return ()


def _matched_error_subcode(
    *,
    content: Any,
    structured_payloads: Sequence[Any],
    tool_name: str,
) -> str | None:
    matched = {
        code
        for text in _iter_content_texts(content)
        if (code := _subcode_from_content_text(text, tool_name=tool_name)) is not None
    }
    matched.update(
        code for payload in structured_payloads for code in _iter_structured_error_codes(payload)
    )
    retryable = sorted(matched.intersection(_RETRYABLE_MCP_SUBCODES))
    if retryable:
        return retryable[0]
    return min(matched) if matched else None


__all__ = [
    "CONTEXT_ENV",
    "McpClient",
    "McpClientError",
    "McpClientTimeout",
    "McpClientTransportError",
    "McpStdioClient",
    "McpToolResultInvalid",
    "McpToolRetryableError",
    "McpToolReturnedError",
]
