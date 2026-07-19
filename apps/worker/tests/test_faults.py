from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from pydantic import BaseModel

from enterprise_doc_core.agents.gateway import ModelTimeoutError
from enterprise_doc_core.config import FaultInjectionSettings
from enterprise_doc_core.jobs import ClaimedJob
from enterprise_doc_worker.faults import (
    FaultController,
    FaultInjectingHandler,
    FaultInjectingMcpClient,
    FaultInjectingModelGateway,
    FaultInjectingMultipartObjectStore,
    InjectedRetryableHandlerError,
)
from enterprise_doc_worker.mcp_client import McpClientTimeout


def _claim() -> ClaimedJob:
    return ClaimedJob(
        job_id=uuid4(),
        attempt_id=uuid4(),
        attempt_number=1,
        tenant_id=uuid4(),
        actor_id=uuid4(),
        worker_id="worker-test",
        lease_token=uuid4(),
        fencing_token=1,
        job_type="document.ingest",
        payload={},
    )


async def test_fault_controller_trigger_schedule_is_deterministic() -> None:
    controller = FaultController(
        FaultInjectionSettings(
            enabled=True,
            target="handler",
            mode="delay",
            trigger_after=1,
            trigger_every=2,
        )
    )

    assert [await controller.before("job") for _ in range(5)] == [
        False,
        True,
        False,
        True,
        False,
    ]


async def test_handler_fault_is_one_shot_and_then_delegates() -> None:
    calls = 0

    async def inner(_: ClaimedJob) -> None:
        nonlocal calls
        calls += 1

    handler = FaultInjectingHandler(
        inner,
        FaultController(
            FaultInjectionSettings(
                enabled=True,
                target="handler",
                mode="retryable",
            )
        ),
    )

    with pytest.raises(InjectedRetryableHandlerError):
        await handler(_claim())
    await handler(_claim())

    assert calls == 1


class FakeGateway:
    async def generate(self, _: object) -> object:
        return "delegated"


async def test_model_fault_uses_stable_gateway_error() -> None:
    gateway = FaultInjectingModelGateway(
        FakeGateway(),  # type: ignore[arg-type]
        FaultController(
            FaultInjectionSettings(
                enabled=True,
                target="model",
                mode="model_timeout",
            )
        ),
    )

    with pytest.raises(ModelTimeoutError):
        await gateway.generate(object())  # type: ignore[arg-type]
    assert await gateway.generate(object()) == "delegated"  # type: ignore[arg-type,comparison-overlap]


class ToolResult(BaseModel):
    value: str


class FakeMcpClient:
    async def call(self, **_: Any) -> ToolResult:
        return ToolResult(value="delegated")


async def test_mcp_fault_uses_stable_client_error() -> None:
    client = FaultInjectingMcpClient(
        FakeMcpClient(),  # type: ignore[arg-type]
        FaultController(
            FaultInjectionSettings(
                enabled=True,
                target="mcp",
                mode="mcp_client_timeout",
            )
        ),
    )

    with pytest.raises(McpClientTimeout):
        await client.call(
            tool_name="search_document",
            request=ToolResult(value="request"),
            result_model=ToolResult,
            context_token="context",
        )
    result = await client.call(
        tool_name="search_document",
        request=ToolResult(value="request"),
        result_model=ToolResult,
        context_token="context",
    )
    assert result == ToolResult(value="delegated")


class FakeMultipartStore:
    async def get_range(self, **_: Any) -> bytes:
        return b"abcdef"


async def test_multipart_short_read_is_one_shot() -> None:
    store = FaultInjectingMultipartObjectStore(
        FakeMultipartStore(),  # type: ignore[arg-type]
        FaultController(
            FaultInjectionSettings(
                enabled=True,
                target="multipart",
                mode="short_read",
            )
        ),
    )

    assert await store.get_range(bucket="documents", key="a", start=0, end_inclusive=5) == b"abcde"
    assert await store.get_range(bucket="documents", key="a", start=0, end_inclusive=5) == b"abcdef"
