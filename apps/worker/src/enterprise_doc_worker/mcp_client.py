from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from pathlib import Path
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
from enterprise_doc_worker.queue import JobHandlerError

CONTEXT_ENV = "ENTERPRISE_DOC_MCP_CONTEXT"
_ResultT = TypeVar("_ResultT", bound=BaseModel)


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
                        return _parse_result(result, result_model)
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


def _secret_value(value: SecretStr | str) -> str:
    return value.get_secret_value() if isinstance(value, SecretStr) else value


def _exception_leaves(error: BaseException) -> tuple[BaseException, ...]:
    if isinstance(error, BaseExceptionGroup):
        return tuple(leaf for nested in error.exceptions for leaf in _exception_leaves(nested))
    return (error,)


def _parse_result[ResultT: BaseModel](result: Any, result_model: type[ResultT]) -> ResultT:
    if bool(getattr(result, "isError", False)):
        raise McpToolReturnedError()
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


__all__ = [
    "CONTEXT_ENV",
    "McpClient",
    "McpClientError",
    "McpClientTimeout",
    "McpClientTransportError",
    "McpStdioClient",
    "McpToolResultInvalid",
    "McpToolReturnedError",
]
