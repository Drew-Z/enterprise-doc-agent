from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from enterprise_doc_core.config import EmbeddingSettings
from enterprise_doc_core.documents.ingestion import EmbeddingProvider


class EmbeddingProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(message)


class OpenAICompatibleEmbeddingProvider:
    """OpenAI-compatible embeddings adapter shared by ingest and retrieval."""

    def __init__(
        self,
        *,
        settings: EmbeddingSettings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if settings.provider.value != "openai_compatible":
            raise ValueError(
                "OpenAICompatibleEmbeddingProvider requires an openai_compatible provider"
            )
        if settings.base_url is None or settings.api_key is None:
            raise ValueError("embedding base URL and API key are required")
        self.settings = settings
        self.api_key = settings.api_key.get_secret_value()
        self.endpoint = f"{settings.base_url.rstrip('/')}/embeddings"
        self.client = client

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        if not texts:
            return ()
        if self.client is not None:
            return await self._embed_with_client(self.client, tuple(texts))
        async with httpx.AsyncClient(trust_env=False) as client:
            return await self._embed_with_client(client, tuple(texts))

    async def _embed_with_client(
        self,
        client: httpx.AsyncClient,
        texts: tuple[str, ...],
    ) -> tuple[tuple[float, ...], ...]:
        output: list[tuple[float, ...]] = []
        for start in range(0, len(texts), self.settings.batch_size):
            batch = texts[start : start + self.settings.batch_size]
            output.extend(await self._embed_batch_with_split(client, batch))
        return tuple(output)

    async def _embed_batch_with_split(
        self,
        client: httpx.AsyncClient,
        texts: tuple[str, ...],
    ) -> tuple[tuple[float, ...], ...]:
        try:
            return await self._request_batch(client, texts)
        except EmbeddingProviderError as error:
            if error.code == "embedding_batch_rejected" and len(texts) > 1:
                midpoint = len(texts) // 2
                left = await self._embed_batch_with_split(client, texts[:midpoint])
                right = await self._embed_batch_with_split(client, texts[midpoint:])
                return left + right
            raise

    async def _request_batch(
        self,
        client: httpx.AsyncClient,
        texts: tuple[str, ...],
    ) -> tuple[tuple[float, ...], ...]:
        body: dict[str, Any] = {"model": self.settings.model_name, "input": list(texts)}
        if self.settings.send_dimensions:
            body["dimensions"] = self.settings.dimension
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        for attempt in range(self.settings.max_retries + 1):
            try:
                response = await client.post(
                    self.endpoint,
                    headers=headers,
                    json=body,
                    timeout=httpx.Timeout(self.settings.timeout_seconds),
                )
            except httpx.TimeoutException as error:
                if attempt < self.settings.max_retries:
                    await asyncio.sleep(self._retry_delay(attempt, None))
                    continue
                raise EmbeddingProviderError(
                    "embedding_timeout", "embedding provider request timed out", retryable=True
                ) from error
            except httpx.RequestError as error:
                if attempt < self.settings.max_retries:
                    await asyncio.sleep(self._retry_delay(attempt, None))
                    continue
                raise EmbeddingProviderError(
                    "embedding_transport_error",
                    "embedding provider request failed",
                    retryable=True,
                ) from error

            if 200 <= response.status_code < 300:
                return self._parse_response(response, expected_count=len(texts))
            if response.status_code in {400, 413, 422}:
                raise EmbeddingProviderError(
                    "embedding_batch_rejected",
                    "embedding provider rejected the batch",
                    retryable=False,
                )
            if response.status_code in {401, 403}:
                raise EmbeddingProviderError(
                    "embedding_auth_failed",
                    "embedding provider authentication failed",
                    retryable=False,
                )
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < self.settings.max_retries:
                    await asyncio.sleep(
                        self._retry_delay(attempt, response.headers.get("Retry-After"))
                    )
                    continue
                raise EmbeddingProviderError(
                    (
                        "embedding_rate_limited"
                        if response.status_code == 429
                        else "embedding_server_error"
                    ),
                    "embedding provider returned a retryable error",
                    retryable=True,
                )
            raise EmbeddingProviderError(
                "embedding_contract_error",
                "embedding provider returned an unsupported status",
                retryable=False,
            )
        raise AssertionError("embedding retry loop did not return")

    def _parse_response(
        self,
        response: httpx.Response,
        *,
        expected_count: int,
    ) -> tuple[tuple[float, ...], ...]:
        try:
            payload = response.json()
            items = payload["data"]
            if not isinstance(items, list):
                raise TypeError
            ordered: list[tuple[int, tuple[float, ...]]] = []
            for item in items:
                index = item["index"]
                values = item["embedding"]
                if (
                    not isinstance(index, int)
                    or isinstance(index, bool)
                    or not isinstance(values, list)
                    or any(
                        not isinstance(value, (int, float)) or isinstance(value, bool)
                        for value in values
                    )
                ):
                    raise TypeError
                vector = tuple(float(value) for value in values)
                if not vector or any(not math.isfinite(value) for value in vector):
                    raise ValueError
                ordered.append((index, vector))
            ordered.sort(key=lambda pair: pair[0])
            if [index for index, _ in ordered] != list(range(len(ordered))):
                raise ValueError
            vectors = tuple(vector for _, vector in ordered)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise EmbeddingProviderError(
                "embedding_contract_error",
                "embedding provider returned an invalid response",
                retryable=False,
            ) from error
        if len(vectors) != expected_count:
            raise EmbeddingProviderError(
                "embedding_batch_rejected",
                "embedding provider returned the wrong item count",
                retryable=False,
            )
        if any(len(vector) != self.settings.dimension for vector in vectors):
            raise EmbeddingProviderError(
                "embedding_dimension_mismatch",
                "embedding provider returned the wrong vector dimension",
                retryable=False,
            )
        return vectors

    def _retry_delay(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), 60.0)
            except ValueError:
                try:
                    date = parsedate_to_datetime(retry_after)
                    if date.tzinfo is None:
                        date = date.replace(tzinfo=UTC)
                    return min(max((date - datetime.now(UTC)).total_seconds(), 0.0), 60.0)
                except (TypeError, ValueError, OverflowError):
                    pass
        delay = float(self.settings.retry_base_seconds) * (2**attempt)
        return float(min(delay, 60.0))


def embedding_model_identity(settings: EmbeddingSettings) -> str:
    if settings.provider.value == "hash":
        return settings.model_name
    if settings.model_revision:
        return f"{settings.model_name}@{settings.model_revision}"
    return settings.model_name


def build_embedding_provider(
    settings: EmbeddingSettings,
) -> tuple[EmbeddingProvider, str, int]:
    from enterprise_doc_core.documents.embedding_routing import DimensionCheckedEmbeddingProvider
    from enterprise_doc_core.documents.ingestion import HashEmbeddingProvider

    if settings.provider.value == "hash":
        provider: EmbeddingProvider = HashEmbeddingProvider(dimension=settings.dimension)
    else:
        provider = OpenAICompatibleEmbeddingProvider(settings=settings)
    return (
        DimensionCheckedEmbeddingProvider(provider, dimension=settings.dimension),
        embedding_model_identity(settings),
        settings.dimension,
    )


__all__ = [
    "EmbeddingProviderError",
    "OpenAICompatibleEmbeddingProvider",
    "build_embedding_provider",
    "embedding_model_identity",
]
