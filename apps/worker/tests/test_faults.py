from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from pydantic import BaseModel

from enterprise_doc_core.agents.gateway import ModelTimeoutError
from enterprise_doc_core.config import FaultInjectionSettings
from enterprise_doc_core.jobs import ClaimedJob
from enterprise_doc_core.object_store.models import ObjectContent, ObjectHead
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


@pytest.mark.parametrize("operation", ["presign_object_put", "retire_upload_object"])
async def test_direct_upload_operations_preserve_one_shot_fault_and_arguments(
    operation: str,
) -> None:
    from enterprise_doc_core.object_store.errors import ObjectStoreUnavailable
    from enterprise_doc_core.object_store.models import PresignedObjectUpload

    calls = []
    signed = PresignedObjectUpload("https://objects.test/signed", {"If-None-Match": "*"}, 60)

    class Inner:
        async def presign_object_put(self, **arguments: Any) -> PresignedObjectUpload:
            calls.append(arguments)
            return signed

        async def retire_upload_object(self, **arguments: Any) -> bool:
            calls.append(arguments)
            return False

    store = FaultInjectingMultipartObjectStore(
        Inner(),
        FaultController(
            FaultInjectionSettings(
                enabled=True,
                target="multipart",
                mode="object_store_unavailable",
            )
        ),
    )
    arguments: dict[str, Any] = {
        "bucket": "documents",
        "key": "owned",
        "metadata": {"contract": "m1"},
    }
    if operation == "presign_object_put":
        arguments.update(size_bytes=37, expires_in_seconds=60)
    method = getattr(store, operation)
    with pytest.raises(ObjectStoreUnavailable):
        await method(**arguments)
    assert calls == []
    result = await method(**arguments)
    assert result == (signed if operation == "presign_object_put" else False)
    assert calls == [arguments]


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


@pytest.mark.parametrize("content", [b"abcdef", None])
async def test_multipart_bounded_read_preserves_metadata_and_one_shot_fault(
    content: bytes | None,
) -> None:
    head = ObjectHead(6, '"etag"', None, "text/plain", {"contract": "m1"})
    calls = []

    class Inner:
        async def read_object(self, **arguments: Any) -> ObjectContent:
            calls.append(arguments)
            return ObjectContent(head=head, content=content)

    store = FaultInjectingMultipartObjectStore(
        Inner(),
        FaultController(
            FaultInjectionSettings(enabled=True, target="multipart", mode="short_read")
        ),
    )
    first = await store.read_object(bucket="documents", key="a", max_bytes=6)
    second = await store.read_object(bucket="documents", key="a", max_bytes=6)
    assert first.head == second.head == head
    assert first.content == (content[:-1] if content is not None else None)
    assert second.content == content
    assert calls == [{"bucket": "documents", "key": "a", "max_bytes": 6}] * 2
