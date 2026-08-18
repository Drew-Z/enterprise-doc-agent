from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from enterprise_doc_core.agents import (
    BehaviorVersions,
    CircuitBreaker,
    CircuitState,
    DeterministicGroundedGateway,
    GroundedEvidence,
    GroundedModelRequest,
    ModelAuthError,
    ModelRouteDescriptor,
    ModelServerError,
    ModelTimeoutError,
    RoutedChatModelGateway,
)
from enterprise_doc_core.agents.models import AgentRunTaskType
from enterprise_doc_core.documents import (
    DimensionCheckedEmbeddingProvider,
    EmbeddingDimensionMismatch,
)


def _request() -> GroundedModelRequest:
    return GroundedModelRequest(
        task_type=AgentRunTaskType.QUESTION_ANSWER,
        user_input="What are the payment terms?",
        evidence=[
            GroundedEvidence(
                chunk_id=uuid4(),
                tenant_id=uuid4(),
                document_version_id=uuid4(),
                generation_id=uuid4(),
                text="Payment is due within thirty days.",
                rank=1,
                score=1.0,
                start_offset=0,
                end_offset=34,
            )
        ],
        behavior_versions=BehaviorVersions(
            graph_version="m4.v1",
            prompt_version="m4.v1",
            tool_schema_version="m4.v1",
        ),
    )


class ScriptedGateway:
    def __init__(self, results: list[Exception | None]) -> None:
        self.results = results
        self.calls = 0
        self.delegate = DeterministicGroundedGateway()

    async def generate(self, request: GroundedModelRequest):
        self.calls += 1
        result = self.results.pop(0) if self.results else None
        if isinstance(result, Exception):
            raise result
        return await self.delegate.generate(request)


class DelayedGateway:
    def __init__(self, *, delay_seconds: float, error: Exception | None = None) -> None:
        self.delay_seconds = delay_seconds
        self.error = error
        self.calls = 0
        self.delegate = DeterministicGroundedGateway()

    async def generate(self, request: GroundedModelRequest):
        self.calls += 1
        await asyncio.sleep(self.delay_seconds)
        if self.error is not None:
            raise self.error
        return await self.delegate.generate(request)


def _descriptor(name: str) -> ModelRouteDescriptor:
    return ModelRouteDescriptor(
        route_id=name,
        provider="deterministic",
        model_name=name,
        model_version="v1",
        model_revision="revision-1",
        quantization="none",
        context_window_tokens=8192,
        embedding_dimension=8,
    )


async def test_provider_health_reports_full_route_identity() -> None:
    routed = RoutedChatModelGateway(
        primary=DeterministicGroundedGateway(),
        primary_descriptor=_descriptor("primary"),
    )

    health = await routed.healthcheck()

    assert health["primary"].available is True
    assert health["primary"].model_version == "v1"
    assert health["primary"].model_revision == "revision-1"
    assert health["primary"].quantization == "none"
    assert health["primary"].context_window_tokens == 8192
    assert health["primary"].embedding_dimension == 8
    assert health["fallback"].error_code == "not_configured"


async def test_routed_gateway_uses_fallback_only_for_retryable_errors() -> None:
    primary = ScriptedGateway([ModelTimeoutError()])
    fallback = ScriptedGateway([None])
    routed = RoutedChatModelGateway(
        primary=primary,
        primary_descriptor=_descriptor("primary"),
        fallback=fallback,
        fallback_descriptor=_descriptor("fallback"),
    )

    output = await routed.generate(_request())

    assert output.answer_text
    assert primary.calls == 1
    assert fallback.calls == 1
    assert routed.fallback_count == 1
    assert output.telemetry.provider_request_count == 1
    assert output.telemetry.fallback_count == 1
    assert output.telemetry.breaker_state == "closed"
    assert output.telemetry.fallback_trigger_code == "model_timeout"


async def test_model_server_error_falls_back_with_complete_route_telemetry() -> None:
    primary = ScriptedGateway([ModelServerError()])
    fallback = ScriptedGateway([None])
    routed = RoutedChatModelGateway(
        primary=primary,
        primary_descriptor=_descriptor("primary"),
        fallback=fallback,
        fallback_descriptor=_descriptor("fallback"),
    )

    output = await routed.generate(_request())

    assert output.answer_text
    assert output.telemetry.provider_request_count == 1
    assert output.telemetry.fallback_count == 1
    assert output.telemetry.breaker_state == "closed"
    assert output.telemetry.fallback_trigger_code == "model_server_error"


async def test_failed_fallback_carries_final_identity_and_attempt_telemetry() -> None:
    primary = ScriptedGateway([ModelServerError()])
    fallback = ScriptedGateway([ModelServerError()])
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=60)
    routed = RoutedChatModelGateway(
        primary=primary,
        primary_descriptor=_descriptor("primary"),
        fallback=fallback,
        fallback_descriptor=_descriptor("fallback"),
        breaker=breaker,
    )

    with pytest.raises(ModelServerError) as caught:
        await routed.generate(_request())

    assert caught.value.identity is not None
    assert caught.value.identity.model_name == "fallback"
    assert caught.value.telemetry.provider_request_count == 2
    assert caught.value.telemetry.fallback_count == 1
    assert caught.value.telemetry.breaker_state == "open"
    assert caught.value.telemetry.fallback_trigger_code == "model_server_error"


async def test_permanent_provider_error_does_not_fallback() -> None:
    primary = ScriptedGateway([ModelAuthError()])
    fallback = ScriptedGateway([None])
    routed = RoutedChatModelGateway(
        primary=primary,
        primary_descriptor=_descriptor("primary"),
        fallback=fallback,
        fallback_descriptor=_descriptor("fallback"),
    )

    with pytest.raises(ModelAuthError) as caught:
        await routed.generate(_request())

    assert fallback.calls == 0
    assert routed.fallback_count == 0
    assert caught.value.telemetry.fallback_trigger_code is None


async def test_primary_and_fallback_share_one_route_deadline() -> None:
    primary = DelayedGateway(
        delay_seconds=0.02,
        error=ModelTimeoutError("primary provider timeout"),
    )
    fallback = DelayedGateway(delay_seconds=1.0)
    routed = RoutedChatModelGateway(
        primary=primary,
        primary_descriptor=_descriptor("primary"),
        fallback=fallback,
        fallback_descriptor=_descriptor("fallback"),
        deadline_seconds=0.06,
    )
    started_at = asyncio.get_running_loop().time()

    with pytest.raises(ModelTimeoutError) as caught:
        await routed.generate(_request())

    elapsed = asyncio.get_running_loop().time() - started_at
    assert elapsed < 0.3
    assert primary.calls == 1
    assert fallback.calls == 1
    assert routed.fallback_count == 1
    assert caught.value.telemetry.fallback_trigger_code == "model_timeout"


async def test_exhausted_route_budget_does_not_invoke_fallback() -> None:
    primary = DelayedGateway(delay_seconds=1.0)
    fallback = ScriptedGateway([None])
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=60)
    routed = RoutedChatModelGateway(
        primary=primary,
        primary_descriptor=_descriptor("primary"),
        fallback=fallback,
        fallback_descriptor=_descriptor("fallback"),
        breaker=breaker,
        deadline_seconds=0.03,
    )

    with pytest.raises(ModelTimeoutError) as caught:
        await routed.generate(_request())

    assert primary.calls == 1
    assert fallback.calls == 0
    assert routed.fallback_count == 0
    assert breaker.state is CircuitState.OPEN
    assert caught.value.telemetry.fallback_trigger_code == "model_timeout"


async def test_circuit_opens_skips_primary_and_recovers_with_one_probe() -> None:
    now = 0.0

    def clock() -> float:
        return now

    primary = ScriptedGateway([ModelTimeoutError(), ModelTimeoutError(), None])
    fallback = ScriptedGateway([None, None, None])
    breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=10, clock=clock)
    routed = RoutedChatModelGateway(
        primary=primary,
        primary_descriptor=_descriptor("primary"),
        fallback=fallback,
        fallback_descriptor=_descriptor("fallback"),
        breaker=breaker,
    )

    await routed.generate(_request())
    await routed.generate(_request())
    assert breaker.state is CircuitState.OPEN

    await routed.generate(_request())
    assert primary.calls == 2
    assert fallback.calls == 3

    now = 11.0
    await routed.generate(_request())
    assert primary.calls == 3
    assert breaker.state is CircuitState.CLOSED


async def test_open_circuit_fallback_uses_route_budget_without_calling_primary() -> None:
    primary = ScriptedGateway([ModelTimeoutError()])
    fallback = DelayedGateway(delay_seconds=0.01)
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=60)
    routed = RoutedChatModelGateway(
        primary=primary,
        primary_descriptor=_descriptor("primary"),
        fallback=fallback,
        fallback_descriptor=_descriptor("fallback"),
        breaker=breaker,
        deadline_seconds=0.1,
    )

    await routed.generate(_request())
    assert breaker.state is CircuitState.OPEN
    output = await routed.generate(_request())

    assert primary.calls == 1
    assert fallback.calls == 2
    assert routed.fallback_count == 2
    assert output.telemetry.fallback_trigger_code == "model_circuit_open"


class ConcurrentPrimaryGateway:
    def __init__(self) -> None:
        self.calls = 0
        self.first_started = asyncio.Event()
        self.release_first = asyncio.Event()
        self.delegate = DeterministicGroundedGateway()

    async def generate(self, request: GroundedModelRequest):
        self.calls += 1
        if self.calls == 1:
            self.first_started.set()
            await self.release_first.wait()
            return await self.delegate.generate(request)
        raise ModelTimeoutError()


async def test_late_success_cannot_close_a_newly_opened_circuit() -> None:
    primary = ConcurrentPrimaryGateway()
    fallback = ScriptedGateway([None])
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=60)
    routed = RoutedChatModelGateway(
        primary=primary,
        primary_descriptor=_descriptor("primary"),
        fallback=fallback,
        fallback_descriptor=_descriptor("fallback"),
        breaker=breaker,
    )

    late_success = asyncio.create_task(routed.generate(_request()))
    await primary.first_started.wait()
    await routed.generate(_request())
    assert breaker.state is CircuitState.OPEN

    primary.release_first.set()
    await late_success
    assert breaker.state is CircuitState.OPEN


class FailingProbeGateway:
    def __init__(self, probe_error: BaseException) -> None:
        self.calls = 0
        self.probe_error = probe_error

    async def generate(self, _: GroundedModelRequest):
        self.calls += 1
        if self.calls == 1:
            raise ModelTimeoutError()
        raise self.probe_error


@pytest.mark.parametrize("probe_error", [RuntimeError("provider bug"), asyncio.CancelledError()])
async def test_half_open_probe_exception_reopens_circuit(probe_error: BaseException) -> None:
    now = 0.0

    def clock() -> float:
        return now

    primary = FailingProbeGateway(probe_error)
    fallback = ScriptedGateway([None])
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=10, clock=clock)
    routed = RoutedChatModelGateway(
        primary=primary,
        primary_descriptor=_descriptor("primary"),
        fallback=fallback,
        fallback_descriptor=_descriptor("fallback"),
        breaker=breaker,
    )
    await routed.generate(_request())
    assert breaker.state is CircuitState.OPEN

    now = 11.0
    with pytest.raises(type(probe_error)):
        await routed.generate(_request())
    assert breaker.state is CircuitState.OPEN


async def test_half_open_probe_route_timeout_reopens_circuit() -> None:
    now = 0.0

    def clock() -> float:
        return now

    primary = ScriptedGateway([ModelTimeoutError()])
    fallback = ScriptedGateway([None])
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=10, clock=clock)
    routed = RoutedChatModelGateway(
        primary=primary,
        primary_descriptor=_descriptor("primary"),
        fallback=fallback,
        fallback_descriptor=_descriptor("fallback"),
        breaker=breaker,
        deadline_seconds=0.03,
    )
    await routed.generate(_request())
    assert breaker.state is CircuitState.OPEN

    now = 11.0
    routed.primary = DelayedGateway(delay_seconds=1.0)
    with pytest.raises(ModelTimeoutError):
        await routed.generate(_request())

    assert breaker.state is CircuitState.OPEN
    assert fallback.calls == 1


class CancellableProbeGateway:
    def __init__(self) -> None:
        self.calls = 0
        self.started = asyncio.Event()

    async def generate(self, _: GroundedModelRequest):
        self.calls += 1
        if self.calls == 1:
            raise ModelTimeoutError()
        self.started.set()
        await asyncio.Event().wait()


async def test_caller_cancellation_is_not_converted_to_route_timeout() -> None:
    now = 0.0

    def clock() -> float:
        return now

    primary = CancellableProbeGateway()
    fallback = ScriptedGateway([None])
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=10, clock=clock)
    routed = RoutedChatModelGateway(
        primary=primary,
        primary_descriptor=_descriptor("primary"),
        fallback=fallback,
        fallback_descriptor=_descriptor("fallback"),
        breaker=breaker,
        deadline_seconds=5.0,
    )
    await routed.generate(_request())
    now = 11.0
    probe = asyncio.create_task(routed.generate(_request()))
    await primary.started.wait()

    probe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await probe

    assert breaker.state is CircuitState.OPEN
    assert fallback.calls == 1


def test_route_deadline_must_be_positive_and_finite() -> None:
    for invalid in (0.0, -1.0, float("inf"), float("nan")):
        with pytest.raises(ValueError, match="route deadline"):
            RoutedChatModelGateway(
                primary=ScriptedGateway([None]),
                primary_descriptor=_descriptor("primary"),
                deadline_seconds=invalid,
            )


class WrongDimensionProvider:
    async def embed(self, texts):
        return tuple((1.0, 2.0) for _ in texts)


async def test_embedding_route_rejects_index_dimension_mismatch() -> None:
    provider = DimensionCheckedEmbeddingProvider(
        WrongDimensionProvider(),  # type: ignore[arg-type]
        dimension=8,
    )

    with pytest.raises(EmbeddingDimensionMismatch):
        await provider.embed(["one"])
