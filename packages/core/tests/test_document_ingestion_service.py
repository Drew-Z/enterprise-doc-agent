from __future__ import annotations

from dataclasses import dataclass

import pytest

from enterprise_doc_core.documents.ingestion_service import (
    DocumentIngestionError,
    IngestionLimits,
    spool_object,
)
from enterprise_doc_core.object_store.models import ObjectHead


@dataclass
class FakeObjectStore:
    content: bytes
    reported_size: int | None = None

    def __post_init__(self) -> None:
        self.ranges: list[tuple[int, int]] = []

    async def head_object(self, *, bucket: str, key: str) -> ObjectHead:
        return ObjectHead(
            size_bytes=self.reported_size if self.reported_size is not None else len(self.content),
            etag="etag",
            checksum_sha256_b64=None,
            content_type="text/plain",
            metadata={},
        )

    async def get_range(self, *, bucket: str, key: str, start: int, end_inclusive: int) -> bytes:
        self.ranges.append((start, end_inclusive))
        return self.content[start : end_inclusive + 1]


@pytest.mark.asyncio
async def test_spool_reads_fixed_ranges_and_rewinds() -> None:
    store = FakeObjectStore(b"abcdefghij")

    spool = await spool_object(
        object_store=store,
        bucket="documents",
        key="tenant/doc.txt",
        limits=IngestionLimits(range_read_bytes=4, max_spool_memory_bytes=4),
    )
    try:
        assert spool.read() == b"abcdefghij"
    finally:
        spool.close()

    assert store.ranges == [(0, 3), (4, 7), (8, 9)]


@pytest.mark.asyncio
async def test_spool_rejects_documents_above_limit_without_reading() -> None:
    store = FakeObjectStore(b"abcdefghij")

    with pytest.raises(DocumentIngestionError) as caught:
        await spool_object(
            object_store=store,
            bucket="documents",
            key="tenant/doc.txt",
            limits=IngestionLimits(max_document_bytes=5),
        )

    assert caught.value.code == "document_too_large"
    assert store.ranges == []


@pytest.mark.asyncio
async def test_spool_detects_object_size_mismatch() -> None:
    store = FakeObjectStore(b"abcdefghij", reported_size=12)

    with pytest.raises(DocumentIngestionError) as caught:
        await spool_object(
            object_store=store,
            bucket="documents",
            key="tenant/doc.txt",
            limits=IngestionLimits(range_read_bytes=4),
        )

    assert caught.value.code == "object_size_mismatch"
