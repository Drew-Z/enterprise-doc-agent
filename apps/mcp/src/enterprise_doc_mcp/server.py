from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from contextvars import Token
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter
from typing import Any

from mcp.server.fastmcp import FastMCP

from enterprise_doc_core.agents import (
    AgentToolService,
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
    SignedExecutionContext,
    ToolExecutionError,
    verify_execution_context,
)
from enterprise_doc_core.config import DatabaseSettings, FoundationSettings
from enterprise_doc_core.context import (
    PrincipalContext,
    RequestContext,
    reset_request_context,
    set_request_context,
)
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.documents import build_embedding_provider
from enterprise_doc_core.documents.retrieval_service import HybridRetrievalService
from enterprise_doc_core.logging import configure_logging
from enterprise_doc_core.object_store import Boto3ArtifactObjectStore
from enterprise_doc_core.telemetry import MetricsRuntime

LOGGER = logging.getLogger(__name__)
CONTEXT_ENV = "ENTERPRISE_DOC_MCP_CONTEXT"


class McpServerError(RuntimeError):
    code = "mcp_server_error"

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


@dataclass(slots=True)
class McpRuntime:
    service: AgentToolService
    signing_secret: str
    request_timeout_seconds: float = 30.0
    context_token: str | None = None
    clock: Callable[[], datetime] | None = None
    metrics: MetricsRuntime | None = None

    def load_context(self) -> SignedExecutionContext:
        token = self.context_token or os.environ.get(CONTEXT_ENV)
        if not token:
            raise McpServerError("execution_context_missing")
        try:
            context = verify_execution_context(
                token,
                self.signing_secret,
                now=self.clock() if self.clock is not None else None,
            )
        except Exception as error:
            code = getattr(error, "code", "execution_context_invalid")
            raise McpServerError(str(code)) from None
        return context

    def bind_request_context(
        self,
        context: SignedExecutionContext,
    ) -> Token[RequestContext | None]:
        return set_request_context(
            RequestContext(
                request_id=str(context.execution_id),
                correlation_id=str(context.run_id),
                principal=PrincipalContext(
                    tenant_id=str(context.tenant_id),
                    actor_id=str(context.actor_id),
                    role="worker",
                ),
            )
        )


def build_server(runtime: McpRuntime) -> FastMCP:
    server = FastMCP(
        name="enterprise-doc-mcp",
        instructions="Stable v1 enterprise document tools.",
        log_level="INFO",
    )

    @server.tool(
        name="search_document",
        description="Search the authorized document version and freeze evidence for this run.",
        structured_output=True,
    )
    async def search_document(request: SearchDocumentInput) -> SearchDocumentResult:
        return await _invoke(runtime, request, runtime.service.search_document)

    @server.tool(
        name="read_chunk",
        description="Read one chunk that is already frozen in the run evidence set.",
        structured_output=True,
    )
    async def read_chunk(request: ReadChunkInput) -> ReadChunkResult:
        return await _invoke(runtime, request, runtime.service.read_chunk)

    @server.tool(
        name="create_draft_artifact",
        description="Create a verified draft artifact from a grounded answer for this run.",
        structured_output=True,
    )
    async def create_draft_artifact(
        request: CreateDraftArtifactInput,
    ) -> CreateDraftArtifactResult:
        return await _invoke(runtime, request, runtime.service.create_draft_artifact)

    @server.tool(
        name="get_artifact",
        description="Get a short-lived download URL for an authorized artifact.",
        structured_output=True,
    )
    async def get_artifact(request: GetArtifactInput) -> GetArtifactResult:
        return await _invoke(runtime, request, runtime.service.get_artifact)

    @server.tool(
        name="publish_artifact",
        description="Publish an exact artifact target with a server-approved owner decision.",
        structured_output=True,
    )
    async def publish_artifact(request: PublishArtifactInput) -> PublishArtifactResult:
        return await _invoke(runtime, request, runtime.service.publish_artifact)

    _enforce_strict_tool_arguments(server)
    return server


def _enforce_strict_tool_arguments(server: FastMCP) -> None:
    """Make FastMCP's generated wrapper model reject unknown top-level fields.

    FastMCP 1.x builds a dynamic argument model around the single ``request``
    parameter. Its default model config ignores unknown wrapper fields, so keep the
    compatibility shim local to the five versioned tools and refresh the published
    schema as well as runtime validation.
    """
    tool_manager = getattr(server, "_tool_manager", None)
    tools = getattr(tool_manager, "_tools", None)
    if not isinstance(tools, dict):
        raise RuntimeError("unsupported FastMCP tool manager")
    for name in (
        "search_document",
        "read_chunk",
        "create_draft_artifact",
        "get_artifact",
        "publish_artifact",
    ):
        tool = tools.get(name)
        model = getattr(getattr(tool, "fn_metadata", None), "arg_model", None)
        if tool is None or model is None:
            raise RuntimeError(f"missing FastMCP tool argument model: {name}")
        model.model_config["extra"] = "forbid"
        model.model_rebuild(force=True)
        # FastMCP validates an already decoded JSON object with model_validate().
        # Re-enter through Pydantic's strict JSON path so canonical UUID strings
        # remain valid without enabling Python-side coercion.
        model.model_validate = classmethod(_validate_wire_arguments)
        tool.parameters = model.model_json_schema(by_alias=True)


def _validate_wire_arguments(
    model: type[Any],
    value: Any,
    /,
    **_: Any,
) -> Any:
    encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    return model.model_validate_json(encoded, strict=True)


async def _invoke[RequestT, ResultT](
    runtime: McpRuntime,
    request: RequestT,
    operation: Callable[[SignedExecutionContext, RequestT], Awaitable[ResultT]],
) -> ResultT:
    started = perf_counter()
    result_label = "error"
    context_token: Token[RequestContext | None] | None = None
    try:
        context = runtime.load_context()
        context_token = runtime.bind_request_context(context)
        async with asyncio.timeout(runtime.request_timeout_seconds):
            result = await operation(context, request)
    except asyncio.CancelledError:
        result_label = "cancelled"
        raise
    except TimeoutError:
        result_label = "timeout"
        raise McpServerError("mcp_tool_timeout") from None
    except McpServerError:
        result_label = "permanent_error"
        raise
    except ToolExecutionError as error:
        result_label = "retryable_error" if error.retryable else "permanent_error"
        raise McpServerError(error.code) from None
    except Exception as error:
        result_label = "error"
        code = getattr(error, "code", "mcp_tool_failed")
        LOGGER.warning("mcp_tool_failed", extra={"event_data": {"error_code": code}})
        raise McpServerError(str(code)) from None
    else:
        result_label = "success"
        return result
    finally:
        if context_token is not None:
            reset_request_context(context_token)
        if runtime.metrics is not None:
            runtime.metrics.observe_boundary(
                boundary="mcp",
                operation="server_call",
                result=result_label,
                duration=perf_counter() - started,
            )


@dataclass(slots=True)
class RuntimeResources:
    runtime: McpRuntime
    engine: object
    artifact_store: Boto3ArtifactObjectStore
    metrics: MetricsRuntime

    async def close(self) -> None:
        await self.artifact_store.close()
        dispose = getattr(self.engine, "dispose", None)
        if callable(dispose):
            await dispose()


def build_runtime(
    settings: FoundationSettings,
    *,
    metrics: MetricsRuntime | None = None,
) -> RuntimeResources:
    resolved_metrics = metrics if metrics is not None else MetricsRuntime.create()
    engine = create_database_engine(_mcp_database_settings(settings))
    session_factory = create_session_factory(engine)
    embedding_provider, embedding_model, embedding_dimension = build_embedding_provider(
        settings.embedding
    )
    retrieval = HybridRetrievalService(
        session_factory=session_factory,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        embedding_dimension=embedding_dimension,
        query_instruction=(
            settings.embedding.query_instruction
            if settings.embedding.provider.value == "openai_compatible"
            else None
        ),
        require_vector_evidence=settings.retrieval.require_vector_evidence,
        metrics=resolved_metrics,
    )
    artifact_store = Boto3ArtifactObjectStore(
        settings=settings.object_store,
        max_object_bytes=settings.agent.max_artifact_bytes,
        metrics=resolved_metrics,
    )
    service = AgentToolService(
        session_factory=session_factory,
        retrieval_service=retrieval,
        artifact_store=artifact_store,
        stale_execution_seconds=max(30, int(settings.mcp.request_timeout_seconds)),
        artifact_bucket=settings.object_store.artifacts_bucket,
    )
    return RuntimeResources(
        runtime=McpRuntime(
            service=service,
            signing_secret=settings.mcp.signing_secret.get_secret_value(),
            request_timeout_seconds=settings.mcp.request_timeout_seconds,
            metrics=resolved_metrics,
        ),
        engine=engine,
        artifact_store=artifact_store,
        metrics=resolved_metrics,
    )


def _mcp_database_settings(settings: FoundationSettings) -> DatabaseSettings:
    database_url = settings.mcp.database_url
    if database_url is None:
        return settings.database
    updates: dict[str, object] = {"url": database_url}
    if settings.mcp.database_transaction_mode:
        # Supavisor transaction mode is intended for short-lived clients such as
        # the per-call stdio MCP process. Keep its client pool singular and
        # disable server-side prepared statements, which transaction mode does
        # not support.
        updates.update(
            pool_size=1,
            max_overflow=0,
            prepare_threshold=None,
        )
    return settings.database.model_copy(update=updates)


async def run_stdio(settings: FoundationSettings | None = None) -> None:
    resolved = settings or FoundationSettings()
    configure_logging(
        service="enterprise-doc-mcp",
        environment=resolved.app_env.value,
        level=resolved.log_level,
    )
    resources = build_runtime(resolved)
    server = build_server(resources.runtime)
    try:
        await server.run_stdio_async()
    finally:
        await resources.close()


__all__ = [
    "CONTEXT_ENV",
    "McpRuntime",
    "McpServerError",
    "RuntimeResources",
    "build_runtime",
    "build_server",
    "run_stdio",
]
