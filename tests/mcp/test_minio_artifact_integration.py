from __future__ import annotations

from uuid import uuid4

import httpx
import pytest

from enterprise_doc_core.config import ObjectStoreSettings
from enterprise_doc_core.object_store import Boto3ArtifactObjectStore


@pytest.mark.integration
async def test_minio_artifact_put_head_presigned_get_delete_round_trip() -> None:
    settings = ObjectStoreSettings()
    adapter = Boto3ArtifactObjectStore(settings=settings, max_object_bytes=1024 * 1024)
    key = f"m4-probe/{uuid4().hex}/artifact.json"
    body = b'{"schema_version":1,"answer":"ok"}'
    try:
        stored = await adapter.put_object(
            bucket=settings.artifacts_bucket,
            key=key,
            body=body,
            content_type="application/json",
            metadata={"kind": "answer"},
        )
        head = await adapter.head_object(bucket=settings.artifacts_bucket, key=key)
        assert head.size_bytes == len(body)
        assert head.metadata["sha256"] == stored.content_sha256

        signed = await adapter.presign_get(
            bucket=settings.artifacts_bucket,
            key=key,
            expires_in_seconds=settings.presign_ttl_seconds,
        )
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            response = await client.get(signed.url)
        assert response.status_code == 200
        assert response.content == body
    finally:
        await adapter.delete_object(bucket=settings.artifacts_bucket, key=key)
        await adapter.close()
