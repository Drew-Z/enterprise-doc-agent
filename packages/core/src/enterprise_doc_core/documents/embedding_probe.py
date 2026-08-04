from __future__ import annotations

import asyncio
import json
import math
from time import perf_counter

from enterprise_doc_core.config import FoundationSettings
from enterprise_doc_core.db import ensure_asyncio_compatibility
from enterprise_doc_core.documents.embedding_provider import (
    build_embedding_provider,
    embedding_model_identity,
)


async def probe(settings: FoundationSettings) -> dict[str, object]:
    provider, _, dimension = build_embedding_provider(settings.embedding)
    started = perf_counter()
    vectors = await provider.embed(
        (
            "Enterprise document retrieval contract probe.",
            "Semantic embedding health check with non-sensitive text.",
        )
    )
    norms = [math.sqrt(sum(value * value for value in vector)) for vector in vectors]
    finite = all(math.isfinite(value) for vector in vectors for value in vector)
    nonzero_norms = len(vectors) == 2 and all(norm > 0 for norm in norms)
    return {
        "status": "passed" if finite and nonzero_norms else "failed",
        "provider": settings.embedding.provider.value,
        "model": embedding_model_identity(settings.embedding),
        "dimension": dimension,
        "version": settings.embedding.version,
        "item_count": len(vectors),
        "finite": finite,
        "nonzero_norms": nonzero_norms,
        "elapsed_ms": round((perf_counter() - started) * 1000, 2),
        "values_redacted": True,
    }


def main() -> None:
    ensure_asyncio_compatibility()
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        report = runner.run(probe(FoundationSettings()))
    print(json.dumps(report, sort_keys=True))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
