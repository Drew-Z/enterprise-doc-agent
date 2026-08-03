from __future__ import annotations

import math
from collections.abc import Sequence

from enterprise_doc_core.documents.ingestion import EmbeddingProvider


class EmbeddingDimensionMismatch(ValueError):
    code = "embedding_dimension_mismatch"


class DimensionCheckedEmbeddingProvider:
    """Keep an embedding route compatible with the configured vector index."""

    def __init__(self, inner: EmbeddingProvider, *, dimension: int) -> None:
        if dimension <= 0:
            raise ValueError("embedding dimension must be positive")
        self.inner = inner
        self.dimension = dimension

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        vectors = await self.inner.embed(texts)
        if len(vectors) != len(texts):
            raise EmbeddingDimensionMismatch("embedding provider returned a wrong item count")
        if any(len(vector) != self.dimension for vector in vectors):
            raise EmbeddingDimensionMismatch(
                f"embedding provider returned vectors outside dimension {self.dimension}"
            )
        if any(
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            for vector in vectors
            for value in vector
        ):
            raise EmbeddingDimensionMismatch("embedding provider returned non-finite values")
        return vectors


__all__ = ["DimensionCheckedEmbeddingProvider", "EmbeddingDimensionMismatch"]
