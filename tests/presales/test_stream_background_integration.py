import asyncio

import httpx
import pytest
from sqlalchemy import select

from enterprise_doc_core.presales.models import PresalesProviderCall
from tests.presales.test_presales_background_integration import (
    background as background,
)
from tests.presales.test_presales_background_integration import (
    browser_db as browser_db,
)
from tests.presales.test_presales_background_integration import (
    configured_worker,
    enqueue,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("stop", ["cancel", "deadline"])
async def test_cancelled_presales_stream_keeps_observed_usage_without_publishing(background, stop):
    entered = asyncio.Event()

    class Stream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield (
                b'data: {"id":"response-1","model":"test-model",'
                b'"choices":[],"usage":{"total_tokens":42}}\n\n'
            )
            entered.set()
            await asyncio.Event().wait()

        async def aclose(self):
            self.closed = True

    stream = Stream()
    b = background
    b.service.generation.gateway.settings.streaming = True
    worker = configured_worker(
        b,
        lambda _: httpx.Response(
            200,
            stream=stream,
            headers={"content-type": "text/event-stream", "x-oneapi-request-id": "request-1"},
        ),
        row_timeout_seconds=5 if stop == "deadline" else 90,
    )
    packet = await enqueue(b)
    task = asyncio.create_task(worker.run_once("stream-worker"))
    await asyncio.wait_for(entered.wait(), 5)
    if stop == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        await asyncio.wait_for(task, 10)
    async with b.sessions() as session:
        calls = (
            await session.scalars(
                select(PresalesProviderCall).order_by(PresalesProviderCall.number)
            )
        ).all()
        call = calls[0]
        assert call.usage == {"total_tokens": 42}
        assert call.provider_response_id == "response-1"
        assert call.provider_request_id == "request-1"
        assert call.state == ("running" if stop == "cancel" else "failed")
        if stop == "deadline":
            assert call.error_code == "presales_model_timeout"
    result = await b.service.get(b.context.principal, packet.id)
    assert result.rows[0].draft is None
    assert stream.closed
