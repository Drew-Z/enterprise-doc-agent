import asyncio
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from tests.browser_sessions.conftest import browser_db as browser_db

from enterprise_doc_core.billing.errors import UsageError
from enterprise_doc_core.billing.provider_calls import ProviderCallService, recorded_post
from enterprise_doc_core.billing.provider_models import ProviderDispatch
from enterprise_doc_core.config import ProviderUsageSettings
from enterprise_doc_core.identity.models import Tenant
from enterprise_doc_core.model_response import ModelResponseError, OpenAIResponseReader

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("mode", ["complete", "truncated", "cancelled", "transport", "unknown"])
async def test_stream_records_once_and_retains_observed_usage_on_failure(browser_db, mode):
    sessions = browser_db.sessions
    tenant_id = uuid4()
    async with sessions.begin() as session:
        session.add(
            Tenant(
                id=tenant_id, name="stream test", slug="stream-" + tenant_id.hex, quota_bytes=1024
            )
        )
    service = ProviderCallService(
        session_factory=sessions, settings=ProviderUsageSettings(agent_call_limit=1)
    )

    async def guard(session):
        pass

    class Stream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            if mode != "unknown":
                yield (
                    b'data: {"id":"response-1","model":"test-model",'
                    b'"choices":[],"usage":{"total_tokens":42}}\n\n'
                )
            if mode == "cancelled":
                raise asyncio.CancelledError()
            if mode == "transport":
                raise httpx.ReadError("private detail")
            if mode == "complete":
                yield (
                    b'data: {"choices":[{"index":0,"delta":{"content":"{}"},'
                    b'"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
                )

        async def aclose(self):
            self.closed = True

    stream = Stream()
    sent = []

    def respond(request):
        sent.append(request)
        return httpx.Response(
            200,
            stream=stream,
            headers={"content-type": "text/event-stream", "x-oneapi-request-id": "request-1"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with service.scope(tenant_id=tenant_id, operation_id=uuid4(), kind="agent", guard=guard):

            async def call():
                return await recorded_post(
                    client,
                    "https://model.invalid/chat/completions",
                    provider="test",
                    model="test",
                    json_body={"stream": True},
                    headers={},
                    request_timeout=httpx.Timeout(1),
                    require_metering=True,
                    response_reader=OpenAIResponseReader(streaming=True, max_bytes=4096),
                )

            if mode == "complete":
                await call()
            else:
                expected = (
                    asyncio.CancelledError
                    if mode == "cancelled"
                    else httpx.ReadError
                    if mode == "transport"
                    else ModelResponseError
                )
                with pytest.raises(expected):
                    await call()
            with pytest.raises(UsageError, match="provider_operation_budget_exhausted"):
                await call()
    assert len(sent) == 1 and stream.closed
    async with sessions() as session:
        row = (
            await session.scalars(
                select(ProviderDispatch).where(ProviderDispatch.tenant_id == tenant_id)
            )
        ).one()
        assert row.total_tokens == (None if mode == "unknown" else 42)
        assert row.provider_request_id == "request-1"
        assert row.provider_response_id == (None if mode == "unknown" else "response-1")
        assert row.estimated_cost is None
        assert row.state == {
            "complete": "responded",
            "cancelled": "cancelled",
            "transport": "transport_error",
        }.get(mode, "unknown")
