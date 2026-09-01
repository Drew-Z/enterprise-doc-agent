from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from enterprise_doc_core.agents import RoutedChatModelGateway
from enterprise_doc_core.config import ModelProvider, ModelSettings
from enterprise_doc_core.telemetry import MetricsRuntime
from enterprise_doc_worker.agent_handler import AgentExecutionContext
from enterprise_doc_worker.agents import (
    AgentGraphExecutionResultInvalid,
    AgentGraphExecutionTimeout,
    AgentGraphExecutor,
    _configured_gateway,
)


def _context(**overrides: Any) -> AgentExecutionContext:
    context = AgentExecutionContext(
        tenant_id=uuid4(),
        actor_id=uuid4(),
        run_id=uuid4(),
        execution_id=uuid4(),
        execution_sequence=0,
        execution_kind="initial",
        job_id=uuid4(),
        attempt_id=uuid4(),
        attempt_number=1,
        worker_id="worker-1",
        lease_token=uuid4(),
        fencing_token=1,
        document_version_id=uuid4(),
        task_type="question_answer",
        publish_requested=False,
        graph_thread_id="",
        graph_version="m4.v2",
        run_status="pending",
        approval_request_id=None,
    )
    return replace(context, graph_thread_id=str(context.run_id), **overrides)


class FakeGraph:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.inputs: list[object] = []
        self.configs: list[object] = []

    async def ainvoke(self, input: object, *, config: object) -> dict[str, Any]:
        self.inputs.append(input)
        self.configs.append(config)
        return self.result


class FakeBackend:
    def __init__(self) -> None:
        self.prepared = 0
        self.waiting_marked = 0

    async def prepare_segment(self) -> None:
        self.prepared += 1

    async def mark_waiting_for_approval(self) -> None:
        self.waiting_marked += 1


class HangingGraph:
    def __init__(self) -> None:
        self.cancelled = False

    async def ainvoke(self, input: object, *, config: object) -> dict[str, Any]:
        del input, config
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class SlowCancellingGraph:
    def __init__(self) -> None:
        self.cancel_count = 0

    async def ainvoke(self, input: object, *, config: object) -> dict[str, Any]:
        del input, config
        while True:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancel_count += 1
                if self.cancel_count >= 2:
                    raise


class NeverCancellingGraph:
    def __init__(self) -> None:
        self.stop = False
        self.task: asyncio.Task[Any] | None = None

    async def ainvoke(self, input: object, *, config: object) -> dict[str, Any]:
        del input, config
        self.task = asyncio.current_task()
        while not self.stop:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                continue
        return {"outcome": "succeeded"}


@pytest.mark.asyncio
async def test_graph_executor_starts_initial_segment_with_json_state() -> None:
    graph = FakeGraph({"outcome": "succeeded"})
    backend = FakeBackend()

    def builder(**_: Any) -> FakeGraph:
        return graph

    context = _context()
    executor = AgentGraphExecutor(
        backend_factory=lambda _: backend,  # type: ignore[arg-type]
        gateway=object(),  # type: ignore[arg-type]
        checkpointer=InMemorySaver(),
        graph_builder=builder,  # type: ignore[arg-type]
    )

    await executor(context)

    state = graph.inputs[0]
    assert isinstance(state, dict)
    assert state["run_id"] == str(context.run_id)
    assert state["graph_version"] == "m4.v2"
    assert graph.configs[0]["configurable"]["thread_id"] == str(context.run_id)
    assert backend.prepared == 1


@pytest.mark.asyncio
async def test_graph_executor_resumes_same_thread_with_approval_command() -> None:
    graph = FakeGraph({"outcome": "succeeded"})

    context = _context(
        execution_kind="resume",
        approval_request_id=uuid4(),
        approval_decision="approved",
        approval_decision_fingerprint="a" * 64,
    )
    executor = AgentGraphExecutor(
        backend_factory=lambda _: FakeBackend(),  # type: ignore[arg-type]
        gateway=object(),  # type: ignore[arg-type]
        checkpointer=InMemorySaver(),
        graph_builder=lambda **_: graph,  # type: ignore[arg-type]
    )

    await executor(context)

    command = graph.inputs[0]
    assert isinstance(command, Command)
    assert command.resume["approval_id"] == str(context.approval_request_id)
    assert graph.configs[0]["configurable"]["thread_id"] == str(context.run_id)


@pytest.mark.asyncio
async def test_graph_executor_accepts_interrupt_as_successful_segment() -> None:
    graph = FakeGraph({"__interrupt__": [{"approval_id": str(uuid4())}]})
    backend = FakeBackend()
    executor = AgentGraphExecutor(
        backend_factory=lambda _: backend,  # type: ignore[arg-type]
        gateway=object(),  # type: ignore[arg-type]
        checkpointer=InMemorySaver(),
        graph_builder=lambda **_: graph,  # type: ignore[arg-type]
    )

    await executor(_context())
    assert backend.waiting_marked == 1


@pytest.mark.asyncio
async def test_graph_executor_rejects_unknown_result_outcome() -> None:
    graph = FakeGraph({"outcome": "unexpected"})
    metrics = MetricsRuntime.create()
    executor = AgentGraphExecutor(
        backend_factory=lambda _: FakeBackend(),  # type: ignore[arg-type]
        gateway=object(),  # type: ignore[arg-type]
        checkpointer=InMemorySaver(),
        graph_builder=lambda **_: graph,  # type: ignore[arg-type]
        metrics=metrics,
    )

    with pytest.raises(AgentGraphExecutionResultInvalid):
        await executor(_context())

    rendered = metrics.render().decode("utf-8")
    assert 'boundary="graph",operation="run",result="permanent_error"' in rendered


@pytest.mark.asyncio
async def test_graph_executor_bounds_and_cancels_the_complete_segment() -> None:
    graph = HangingGraph()
    metrics = MetricsRuntime.create()
    executor = AgentGraphExecutor(
        backend_factory=lambda _: FakeBackend(),  # type: ignore[arg-type]
        gateway=object(),  # type: ignore[arg-type]
        checkpointer=InMemorySaver(),
        execution_timeout_seconds=0.01,
        graph_builder=lambda **_: graph,  # type: ignore[arg-type]
        metrics=metrics,
    )

    with pytest.raises(AgentGraphExecutionTimeout) as caught:
        await executor(_context())

    assert caught.value.code == "agent_graph_timeout"
    assert graph.cancelled is True
    rendered = metrics.render().decode("utf-8")
    assert 'boundary="graph",operation="run",result="timeout"' in rendered


@pytest.mark.asyncio
async def test_graph_executor_repeats_cancellation_when_cleanup_swallows_first_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = SlowCancellingGraph()
    original_wait = asyncio.wait
    wait_calls = 0

    async def fast_wait(*args: Any, **kwargs: Any) -> Any:
        nonlocal wait_calls
        wait_calls += 1
        if wait_calls == 2:
            kwargs["timeout"] = 0.01
        return await original_wait(*args, **kwargs)

    monkeypatch.setattr(asyncio, "wait", fast_wait)
    executor = AgentGraphExecutor(
        backend_factory=lambda _: FakeBackend(),  # type: ignore[arg-type]
        gateway=object(),  # type: ignore[arg-type]
        checkpointer=InMemorySaver(),
        execution_timeout_seconds=0.01,
        graph_builder=lambda **_: graph,  # type: ignore[arg-type]
    )

    with pytest.raises(AgentGraphExecutionTimeout):
        await executor(_context())

    assert graph.cancel_count == 2


@pytest.mark.asyncio
async def test_graph_executor_keeps_timeout_bounded_when_cancellation_never_converges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = NeverCancellingGraph()
    original_wait = asyncio.wait

    async def fast_wait(*args: Any, **kwargs: Any) -> Any:
        kwargs["timeout"] = 0.01
        return await original_wait(*args, **kwargs)

    monkeypatch.setattr(asyncio, "wait", fast_wait)
    executor = AgentGraphExecutor(
        backend_factory=lambda _: FakeBackend(),  # type: ignore[arg-type]
        gateway=object(),  # type: ignore[arg-type]
        checkpointer=InMemorySaver(),
        execution_timeout_seconds=0.01,
        graph_builder=lambda **_: graph,  # type: ignore[arg-type]
    )

    started = asyncio.get_running_loop().time()
    with pytest.raises(AgentGraphExecutionTimeout):
        await executor(_context())

    assert asyncio.get_running_loop().time() - started < 0.5
    graph.stop = True
    assert graph.task is not None
    graph.task.cancel()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_graph_executor_cleans_up_segment_when_outer_call_is_cancelled() -> None:
    graph = HangingGraph()
    executor = AgentGraphExecutor(
        backend_factory=lambda _: FakeBackend(),  # type: ignore[arg-type]
        gateway=object(),  # type: ignore[arg-type]
        checkpointer=InMemorySaver(),
        execution_timeout_seconds=10,
        graph_builder=lambda **_: graph,  # type: ignore[arg-type]
    )

    task = asyncio.create_task(executor(_context()))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert graph.cancelled is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected_result"),
    [("succeeded", "success"), ("refused", "refused")],
)
async def test_graph_executor_records_bounded_outcomes(
    outcome: str,
    expected_result: str,
) -> None:
    metrics = MetricsRuntime.create()
    executor = AgentGraphExecutor(
        backend_factory=lambda _: FakeBackend(),  # type: ignore[arg-type]
        gateway=object(),  # type: ignore[arg-type]
        checkpointer=InMemorySaver(),
        graph_builder=lambda **_: FakeGraph({"outcome": outcome}),  # type: ignore[arg-type]
        metrics=metrics,
    )

    await executor(_context())

    rendered = metrics.render().decode("utf-8")
    assert f'boundary="graph",operation="run",result="{expected_result}"' in rendered


def test_configured_gateway_uses_explicit_route_deadline() -> None:
    gateway = _configured_gateway(
        ModelSettings(
            fallback_provider=ModelProvider.DETERMINISTIC,
            route_deadline_seconds=12.5,
        )
    )

    assert isinstance(gateway, RoutedChatModelGateway)
    assert gateway.deadline_seconds == 12.5


def test_configured_gateway_uses_compatible_combined_timeout_by_default() -> None:
    gateway = _configured_gateway(
        ModelSettings(
            fallback_provider=ModelProvider.DETERMINISTIC,
            timeout_seconds=20,
            fallback_timeout_seconds=7,
        )
    )

    assert isinstance(gateway, RoutedChatModelGateway)
    assert gateway.deadline_seconds == 27
