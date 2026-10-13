import asyncio
import json

import pytest

from enterprise_doc_core.config import EmbeddingSettings
from enterprise_doc_core.documents.embedding_provider import managed_embedding_provider


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_kind", ["normal", "error", "cancel"])
async def test_embedding_service_reuses_connection_and_closes_on_exit(exit_kind: str) -> None:
    connections = 0
    requests = 0
    closed = asyncio.Event()
    ready = asyncio.Event()
    handlers: set[asyncio.Task[None]] = set()

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal connections, requests
        connections += 1
        try:
            while True:
                try:
                    headers = await reader.readuntil(b"\r\n\r\n")
                except asyncio.IncompleteReadError:
                    return
                length = next(
                    int(line.split(b":", 1)[1])
                    for line in headers.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                )
                payload = json.loads(await reader.readexactly(length))
                assert payload["input"] == ["synthetic query"]
                requests += 1
                body = json.dumps({"data": [{"index": 0, "embedding": [1.0] * 1024}]}).encode()
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                    + str(len(body)).encode()
                    + b"\r\n\r\n"
                    + body
                )
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            closed.set()

    def connected(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        handlers.add(asyncio.create_task(serve(reader, writer)))

    server = await asyncio.start_server(connected, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    settings = EmbeddingSettings(
        provider="openai_compatible",
        base_url=f"http://127.0.0.1:{port}/v1",
        api_key="local-test-only",
        dimension=1024,
        max_retries=0,
        timeout_seconds=2,
    )

    async def use_provider() -> None:
        async with managed_embedding_provider(settings) as (provider, _, dimension):
            assert dimension == 1024
            for _ in range(2):
                assert len((await provider.embed(("synthetic query",)))[0]) == 1024
            assert connections == 1
            assert requests == 2
            ready.set()
            if exit_kind == "error":
                raise RuntimeError("synthetic service failure")
            if exit_kind == "cancel":
                await asyncio.Event().wait()

    task = asyncio.create_task(use_provider())
    try:
        async with asyncio.timeout(5):
            if exit_kind == "cancel":
                await ready.wait()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            elif exit_kind == "error":
                with pytest.raises(RuntimeError, match="synthetic service failure"):
                    await task
            else:
                await task
            await closed.wait()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        server.close()
        await server.wait_closed()
        for handler in handlers:
            if not handler.done():
                handler.cancel()
        await asyncio.gather(*handlers)
