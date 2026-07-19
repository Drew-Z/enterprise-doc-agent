from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from enterprise_doc_core.agents import (
    AGENT_GRAPH_VERSION,
    AgentGraphBackend,
    AgentGraphError,
    ChatModelGateway,
    CircuitBreaker,
    ModelRouteDescriptor,
    RoutedChatModelGateway,
    build_agent_graph,
    graph_config,
    initial_graph_state,
)
from enterprise_doc_core.agents.gateway import (
    DeterministicGroundedGateway,
    OpenAICompatibleChatGateway,
)
from enterprise_doc_core.config import (
    FaultInjectionSettings,
    McpSettings,
    ModelProvider,
    ModelSettings,
)
from enterprise_doc_core.telemetry import InstrumentedModelGateway, MetricsRuntime
from enterprise_doc_worker.agent_backend import DurableAgentGraphBackend
from enterprise_doc_worker.agent_handler import (
    AgentExecutionContext,
    AgentExecutionContractMismatch,
    AgentExecutionHandler,
    build_agent_execution_handler,
)
from enterprise_doc_worker.faults import wrap_mcp_client, wrap_model_gateway
from enterprise_doc_worker.mcp_client import McpClient, McpStdioClient


class AgentGraphExecutionResultInvalid(AgentGraphError):
    code = "agent_graph_result_invalid"


class CompiledGraph(Protocol):
    async def ainvoke(
        self,
        input: Mapping[str, Any] | Command[Any],
        *,
        config: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


GraphBuilder = Callable[
    ...,
    CompiledStateGraph[Any, Any, Any, Any],
]


def _provider_gateway(settings: ModelSettings) -> ChatModelGateway:
    if settings.provider is ModelProvider.DETERMINISTIC:
        return DeterministicGroundedGateway()
    return OpenAICompatibleChatGateway(settings=settings)


def _route_descriptor(settings: ModelSettings) -> ModelRouteDescriptor:
    return ModelRouteDescriptor(
        route_id=settings.route_id,
        provider=settings.provider.value,
        model_name=settings.model_name or "deterministic-grounded",
        model_version=settings.model_version
        or ("m4.v1" if settings.provider is ModelProvider.DETERMINISTIC else None),
        model_revision=settings.model_revision,
        quantization=settings.quantization,
        context_window_tokens=settings.context_window_tokens,
        embedding_dimension=settings.embedding_dimension,
    )


def _configured_gateway(settings: ModelSettings) -> ChatModelGateway:
    primary = _provider_gateway(settings)
    if settings.fallback_provider is None:
        return primary
    fallback_timeout_seconds = settings.fallback_timeout_seconds or settings.timeout_seconds
    fallback_settings = ModelSettings(
        provider=settings.fallback_provider,
        base_url=settings.fallback_base_url,
        api_key=settings.fallback_api_key,
        model_name=settings.fallback_model_name,
        model_version=settings.fallback_model_version,
        route_id=f"{settings.route_id}-fallback",
        embedding_dimension=settings.embedding_dimension,
        timeout_seconds=fallback_timeout_seconds,
        max_output_bytes=settings.max_output_bytes,
    )
    fallback = _provider_gateway(fallback_settings)
    route_deadline_seconds = settings.route_deadline_seconds
    if route_deadline_seconds is None:
        route_deadline_seconds = settings.timeout_seconds + fallback_timeout_seconds
    return RoutedChatModelGateway(
        primary=primary,
        primary_descriptor=_route_descriptor(settings),
        fallback=fallback,
        fallback_descriptor=_route_descriptor(fallback_settings),
        breaker=CircuitBreaker(
            failure_threshold=settings.circuit_failure_threshold,
            cooldown_seconds=settings.circuit_cooldown_seconds,
        ),
        deadline_seconds=route_deadline_seconds,
    )


BackendFactory = Callable[[AgentExecutionContext], AgentGraphBackend]


class AgentGraphExecutor:
    """Run one durable Agent execution segment on the shared graph thread."""

    def __init__(
        self,
        *,
        backend_factory: BackendFactory,
        gateway: ChatModelGateway,
        checkpointer: BaseCheckpointSaver[Any],
        graph_version: str = AGENT_GRAPH_VERSION,
        graph_builder: GraphBuilder = build_agent_graph,
    ) -> None:
        self.backend_factory = backend_factory
        self.gateway = gateway
        self.checkpointer = checkpointer
        self.graph_version = graph_version
        self.graph_builder = graph_builder

    async def __call__(self, context: AgentExecutionContext) -> None:
        if context.graph_version != self.graph_version:
            raise AgentExecutionContractMismatch()
        backend = self.backend_factory(context)
        prepare_segment = getattr(backend, "prepare_segment", None)
        if callable(prepare_segment):
            await prepare_segment()
        if context.execution_kind == "initial" and context.run_status == "waiting_approval":
            # The checkpoint and business wait projection were committed before
            # this retry was claimed; only settle the durable Job again.
            return
        graph = cast(
            CompiledGraph,
            self.graph_builder(
                backend=backend,
                gateway=self.gateway,
                checkpointer=self.checkpointer,
                graph_version=self.graph_version,
            ),
        )
        config = graph_config(context.graph_thread_id)
        if context.execution_kind == "initial":
            graph_input: Mapping[str, Any] | Command[Any] = initial_graph_state(
                run_id=context.run_id,
                tenant_id=context.tenant_id,
                actor_id=context.actor_id,
                document_version_id=context.document_version_id,
                task_type=context.task_type,
                publish_requested=context.publish_requested,
                graph_version=context.graph_version,
            )
        elif context.execution_kind == "resume":
            if (
                context.approval_request_id is None
                or context.approval_decision is None
                or context.approval_decision_fingerprint is None
            ):
                raise AgentExecutionContractMismatch()
            graph_input = Command(
                resume={
                    "approval_id": str(context.approval_request_id),
                    "decision": context.approval_decision,
                    "decision_fingerprint": context.approval_decision_fingerprint,
                }
            )
        else:
            raise AgentExecutionContractMismatch()

        result = await graph.ainvoke(graph_input, config=config)
        if not isinstance(result, Mapping):
            raise AgentGraphExecutionResultInvalid()
        if "__interrupt__" in result:
            mark_waiting = getattr(backend, "mark_waiting_for_approval", None)
            if not callable(mark_waiting):
                raise AgentGraphExecutionResultInvalid()
            await mark_waiting()
            return
        outcome = result.get("outcome")
        if outcome not in {"succeeded", "refused", "rejected", "expired"}:
            raise AgentGraphExecutionResultInvalid()


def build_agent_graph_executor(
    *,
    backend_factory: BackendFactory,
    gateway: ChatModelGateway,
    checkpointer: BaseCheckpointSaver[Any],
    graph_version: str = AGENT_GRAPH_VERSION,
) -> AgentGraphExecutor:
    return AgentGraphExecutor(
        backend_factory=backend_factory,
        gateway=gateway,
        checkpointer=checkpointer,
        graph_version=graph_version,
    )


def build_durable_agent_handler(
    *,
    session_factory: Any,
    model_settings: ModelSettings,
    mcp_settings: McpSettings,
    checkpointer: BaseCheckpointSaver[Any],
    graph_version: str = AGENT_GRAPH_VERSION,
    gateway: ChatModelGateway | None = None,
    mcp_client: McpClient | None = None,
    fault_injection: FaultInjectionSettings | None = None,
    metrics: MetricsRuntime | None = None,
) -> AgentExecutionHandler:
    if gateway is not None:
        resolved_gateway = gateway
    else:
        resolved_gateway = _configured_gateway(model_settings)
    resolved_mcp_client = mcp_client or McpStdioClient(
        command=mcp_settings.command,
        request_timeout_seconds=mcp_settings.request_timeout_seconds,
    )
    resolved_faults = fault_injection or FaultInjectionSettings()
    resolved_gateway = wrap_model_gateway(resolved_gateway, resolved_faults)
    if metrics is not None:
        resolved_gateway = cast(
            ChatModelGateway,
            InstrumentedModelGateway(resolved_gateway, metrics),
        )
    resolved_mcp_client = wrap_mcp_client(resolved_mcp_client, resolved_faults)

    def backend_factory(context: AgentExecutionContext) -> AgentGraphBackend:
        return DurableAgentGraphBackend(
            session_factory=session_factory,
            context=context,
            gateway=resolved_gateway,
            mcp_client=resolved_mcp_client,
            mcp_settings=mcp_settings,
        )

    executor = build_agent_graph_executor(
        backend_factory=backend_factory,
        gateway=resolved_gateway,
        checkpointer=checkpointer,
        graph_version=graph_version,
    )
    return build_agent_execution_handler(session_factory=session_factory, executor=executor)


__all__ = [
    "AgentGraphExecutionResultInvalid",
    "AgentGraphExecutor",
    "build_agent_graph_executor",
    "build_durable_agent_handler",
]
