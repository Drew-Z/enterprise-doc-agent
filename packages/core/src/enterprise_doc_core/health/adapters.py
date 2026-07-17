from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import redis.asyncio as redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from enterprise_doc_core.config import FoundationSettings
from enterprise_doc_core.db import create_database_engine
from enterprise_doc_core.health.models import ComponentStatus, HealthChecker
from enterprise_doc_core.object_store import Boto3MultipartObjectStore, create_s3_client


class DatabaseChecker:
    name = "database"

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def check(self) -> ComponentStatus:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return ComponentStatus.UP


class RedisChecker:
    name = "redis"

    def __init__(self, client: redis.Redis) -> None:
        self.client = client

    async def check(self) -> ComponentStatus:
        await self.client.ping()
        return ComponentStatus.UP


class ObjectStoreChecker:
    name = "object_store"

    def __init__(self, client: Any, buckets: tuple[str, ...]) -> None:
        self.client = client
        self.buckets = buckets

    async def check(self) -> ComponentStatus:
        for bucket in self.buckets:
            await asyncio.to_thread(self.client.head_bucket, Bucket=bucket)
        return ComponentStatus.UP


@dataclass(slots=True)
class FoundationResources:
    database_engine: AsyncEngine
    redis_client: redis.Redis
    object_store_client: Any
    multipart_object_store: Boto3MultipartObjectStore
    checkers: tuple[HealthChecker, ...]

    async def close(self) -> None:
        await self.database_engine.dispose()
        await self.redis_client.aclose()
        await self.multipart_object_store.close()


def build_foundation_resources(settings: FoundationSettings) -> FoundationResources:
    database_engine = create_database_engine(settings.database)
    redis_client = redis.from_url(
        settings.redis.url.get_secret_value(),
        socket_connect_timeout=settings.redis.connect_timeout_seconds,
        decode_responses=True,
    )
    object_store_client = create_s3_client(
        settings.object_store,
        endpoint_url=settings.object_store.endpoint,
    )
    presign_client = create_s3_client(
        settings.object_store,
        endpoint_url=settings.object_store.presign_endpoint,
    )
    multipart_object_store = Boto3MultipartObjectStore(
        settings=settings.object_store,
        control_client=object_store_client,
        presign_client=presign_client,
    )
    checkers: tuple[HealthChecker, ...] = (
        DatabaseChecker(database_engine),
        RedisChecker(redis_client),
        ObjectStoreChecker(
            object_store_client,
            (
                settings.object_store.documents_bucket,
                settings.object_store.artifacts_bucket,
            ),
        ),
    )
    return FoundationResources(
        database_engine=database_engine,
        redis_client=redis_client,
        object_store_client=object_store_client,
        multipart_object_store=multipart_object_store,
        checkers=checkers,
    )
