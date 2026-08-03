from __future__ import annotations

import json

import httpx
import pytest

from enterprise_doc_core.config import EmbeddingProviderKind, EmbeddingSettings
from enterprise_doc_core.documents import (
    EmbeddingProviderError,
    OpenAICompatibleEmbeddingProvider,
)


def _settings(**overrides: object) -> EmbeddingSettings:
    values: dict[str, object] = {
        "provider": EmbeddingProviderKind.OPENAI_COMPATIBLE,
        "base_url": "https://embedding.example.test/v1",
        "api_key": "embedding-test-key",
        "model_name": "Qwen/Qwen3-Embedding-4B",
        "dimension": 1024,
        "batch_size": 8,
        "max_retries": 2,
    }
    values.update(overrides)
    return EmbeddingSettings(**values)


def _response(request: httpx.Request, vectors: list[list[float]]) -> httpx.Response:
    return httpx.Response(
        200,
        request=request,
        json={
            "model": "Qwen/Qwen3-Embedding-4B",
            "data": [
                {"index": index, "embedding": vector}
                for index, vector in enumerate(vectors)
            ],
        },
    )


@pytest.mark.asyncio
async def test_openai_compatible_embedding_reorders_results_and_sends_dimensions() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(
            200,
            request=request,
            json={
                "data": [
                    {"index": 1, "embedding": [0.0] * 1024},
                    {"index": 0, "embedding": [1.0] + [0.0] * 1023},
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        provider = OpenAICompatibleEmbeddingProvider(settings=_settings(), client=client)
        vectors = await provider.embed(("first", "second"))
    finally:
        await client.aclose()

    assert len(vectors) == 2
    assert vectors[0][0] == 1.0
    assert vectors[1][0] == 0.0
    assert requests == [
        {
            "model": "Qwen/Qwen3-Embedding-4B",
            "input": ["first", "second"],
            "dimensions": 1024,
        }
    ]


@pytest.mark.asyncio
async def test_openai_compatible_embedding_retries_rate_limit() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, request=request, headers={"Retry-After": "0"})
        return _response(request, [[1.0] + [0.0] * 1023])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        provider = OpenAICompatibleEmbeddingProvider(
            settings=_settings(retry_base_seconds=0.001), client=client
        )
        vectors = await provider.embed(("text",))
    finally:
        await client.aclose()

    assert attempts == 2
    assert len(vectors[0]) == 1024


@pytest.mark.asyncio
async def test_openai_compatible_embedding_splits_rejected_batches() -> None:
    batch_sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        values = payload["input"]
        batch_sizes.append(len(values))
        if len(values) > 1:
            return httpx.Response(413, request=request)
        return _response(request, [[1.0] + [0.0] * 1023])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        provider = OpenAICompatibleEmbeddingProvider(settings=_settings(), client=client)
        vectors = await provider.embed(("a", "b"))
    finally:
        await client.aclose()

    assert len(vectors) == 2
    assert batch_sizes == [2, 1, 1]


@pytest.mark.asyncio
async def test_openai_compatible_embedding_rejects_wrong_dimension() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _response(request, [[1.0, 0.0]])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        provider = OpenAICompatibleEmbeddingProvider(settings=_settings(), client=client)
        with pytest.raises(EmbeddingProviderError) as caught:
            await provider.embed(("text",))
    finally:
        await client.aclose()

    assert caught.value.code == "embedding_dimension_mismatch"
    assert caught.value.retryable is False
