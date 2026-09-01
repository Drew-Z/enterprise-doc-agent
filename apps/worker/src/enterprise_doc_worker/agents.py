from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from time import perf_counter
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
from enterprise_doc_worker.mcp_client import InstrumentedMcpClient, McpClient, McpStdioClient


class AgentGraphExecutionResultInvalid(AgentGraphError):
    code = "agent_graph_result_invalid"


class AgentGraphExecutionTimeout(AgentGraphError):
    code = "agent_graph_timeout"


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
        or (AGENT_GRAPH_VERSION if settings.provider is ModelProvider.DETERMINISTIC else None),
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
        execution_timeout_seconds: float = 300.0,
        graph_builder: GraphBuilder = build_agent_graph,
        metrics: MetricsRuntime | None = None,
    ) -> None:
        if execution_timeout_seconds <= 0:
            raise ValueError("execution timeout must be positive")
        self.backend_factory = backend_factory
        self.gateway = gateway
        self.checkpointer = checkpointer
        self.graph_version = graph_version
        self.execution_timeout_seconds = execution_timeout_seconds
        self.graph_builder = graph_builder
        self.metrics = metrics

    @staticmethod
    def _consume_task_result(task: asyncio.Task[Any]) -> None:
        """Consume a detached task's terminal exception to avoid loop warnings."""
        if not task.done():
            return
        try:
            task.exception()
        except (asyncio.CancelledError, Exception):
            pass

    @classmethod
    async def _cancel_execution(cls, execution: asyncio.Task[Any]) -> bool:
        """Cancel a graph task with two bounded cleanup attempts.

        A graph or one of its async cleanup layers can swallow a cancellation.
        The caller must still regain control, so a second cancellation is sent
        after a fixed grace window. If the task remains pending, it is detached
        with a callback that consumes its eventual result.
        """
        for _ in range(2):
            if execution.done():
                cls._consume_task_result(execution)
                return True
            execution.cancel()
            done, _ = await asyncio.wait({execution}, timeout=1.0)
            if done:
                cls._consume_task_result(execution)
                return True
        execution.cancel()
        if execution.done():
            cls._consume_task_result(execution)
            return True
        execution.add_done_callback(cls._consume_task_result)
        return False

    async def __call__(self, context: AgentExecutionContext) -> None:
        started = perf_counter()
        result_label = "error"
        execution: asyncio.Task[Any] | None = None
        try:
            execution = asyncio.create_task(self._execute(context))
            done, _ = await asyncio.wait(
                {execution},
                timeout=self.execution_timeout_seconds,
            )
            if done:
                result_label = execution.result()
            else:
                result_label = "timeout"
                await self._cancel_execution(execution)
                raise AgentGraphExecutionTimeout()
        except asyncio.CancelledError:
            result_label = "cancelled"
            if execution is not None and not execution.done():
                try:
                    await self._cancel_execution(execution)
                except asyncio.CancelledError:
                    # Preserve the caller's cancellation after making a best
                    # effort to stop the graph task.
                    pass
            raise
        except AgentGraphExecutionTimeout:
            result_label = "timeout"
            raise
        except AgentGraphError:
            result_label = "permanent_error"
            raise
        finally:
            if self.metrics is not None:
                self.metrics.observe_boundary(
                    boundary="graph",
                    operation="run",
                    result=result_label,
                    duration=perf_counter() - started,
                )

    async def _execute(self, context: AgentExecutionContext) -> str:
        if context.graph_version != self.graph_version:
            raise AgentExecutionContractMismatch()
        backend = self.backend_factory(context)
        prepare_segment = getattr(backend, "prepare_segment", None)
        if callable(prepare_segment):
            await prepare_segment()
        if context.execution_kind == "initial" and context.run_status == "waiting_approval":
            # The checkpoint and business wait projection were committed before
            # this retry was claimed; only settle the durable Job again.
            return "success"
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
            return "success"
        outcome = result.get("outcome")
        if outcome not in {"succeeded", "refused", "rejected", "expired"}:
            raise AgentGraphExecutionResultInvalid()
        return "success" if outcome == "succeeded" else "refused"


def build_agent_graph_executor(
    *,
    backend_factory: BackendFactory,
    gateway: ChatModelGateway,
    checkpointer: BaseCheckpointSaver[Any],
    graph_version: str = AGENT_GRAPH_VERSION,
    execution_timeout_seconds: float = 300.0,
    metrics: MetricsRuntime | None = None,
) -> AgentGraphExecutor:
    return AgentGraphExecutor(
        backend_factory=backend_factory,
        gateway=gateway,
        checkpointer=checkpointer,
        graph_version=graph_version,
        execution_timeout_seconds=execution_timeout_seconds,
        metrics=metrics,
    )


def build_durable_agent_handler(
    *,
    session_factory: Any,
    model_settings: ModelSettings,
    mcp_settings: McpSettings,
    checkpointer: BaseCheckpointSaver[Any],
    graph_version: str = AGENT_GRAPH_VERSION,
    execution_timeout_seconds: float = 300.0,
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
    if metrics is not None:
        resolved_mcp_client = InstrumentedMcpClient(resolved_mcp_client, metrics)

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
        execution_timeout_seconds=execution_timeout_seconds,
        metrics=metrics,
    )
    return build_agent_execution_handler(session_factory=session_factory, executor=executor)


__all__ = [
    "AgentGraphExecutionResultInvalid",
    "AgentGraphExecutionTimeout",
    "AgentGraphExecutor",
    "build_agent_graph_executor",
    "build_durable_agent_handler",
]
