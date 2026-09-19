"""Loopback acceptance harness: real JWT/upload/MinIO/Outbox/Redis/Celery.

Only the model HTTP transport and embeddings are controlled. Document versions,
generations and chunks must be produced by the normal upload and worker code.
This module is never imported or registered by the production API.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

import httpx
import uvicorn
from celery.contrib.testing.worker import start_worker
from fastapi import Header, HTTPException, Response
from pydantic import SecretStr
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_api.app import create_app
from enterprise_doc_api.auth.bootstrap import BootstrapResult, bootstrap_principal
from enterprise_doc_api.config import ApiServerSettings, ApiSettings, AuthSettings
from enterprise_doc_core.config import EmbeddingSettings, ModelProvider, ModelSettings
from enterprise_doc_core.db import create_session_factory, selector_event_loop_factory
from enterprise_doc_core.documents import Document, DocumentVersion, HashEmbeddingProvider
from enterprise_doc_core.documents.models import DocumentChunk, DocumentIngestionGeneration
from enterprise_doc_core.documents.retrieval_service import HybridRetrievalService
from enterprise_doc_core.health import build_foundation_resources
from enterprise_doc_core.identity import MembershipRole, Tenant, User
from enterprise_doc_core.jobs import ClaimedOutboxEvent, JobRuntimeService, OutboxService
from enterprise_doc_core.jobs.models import Job, JobAttempt, OutboxEvent
from enterprise_doc_core.presales.gateway import OpenAICompatiblePresalesGateway
from enterprise_doc_core.presales.service import PresalesService
from enterprise_doc_core.presales.settings import PresalesSettings
from enterprise_doc_core.uploads.models import UploadSession
from enterprise_doc_worker.config import WorkerSettings
from enterprise_doc_worker.consumer_main import build_consumer_app
from enterprise_doc_worker.publisher import OutboxPublisher
from enterprise_doc_worker.queue import JOB_QUEUE_NAME, CeleryTaskDispatcher
from tests.presales.ingestion_fixtures import build_upload_fixtures
from tests.presales.model_fixture import UploadedEvidenceModel


class TenantOutboxStore:
    """Select test-owned IDs, then use the production claim/lease implementation."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], tenant_id: UUID) -> None:
        self.sessions, self.tenant_id = sessions, tenant_id
        self.service = OutboxService(session_factory=sessions)
        self.claimed_ids: list[str] = []

    async def claim(
        self, *, publisher_id: str, limit: int = 20, event_id: UUID | None = None
    ) -> tuple[ClaimedOutboxEvent, ...]:
        if limit <= 0:
            return ()
        query = select(OutboxEvent.id).where(OutboxEvent.tenant_id == self.tenant_id)
        if event_id is not None:
            query = query.where(OutboxEvent.id == event_id)
        async with self.sessions() as session:
            ids = (await session.scalars(query.order_by(OutboxEvent.created_at))).all()
        result: list[ClaimedOutboxEvent] = []
        for candidate in ids:
            claimed = await self.service.claim(publisher_id=publisher_id, event_id=candidate)
            for item in claimed:
                if item.tenant_id != self.tenant_id:
                    raise RuntimeError("outbox tenant boundary violated")
                self.claimed_ids.append(str(item.event_id))
                result.append(item)
            if len(result) >= limit:
                break
        return tuple(result)

    async def mark_published(self, event: ClaimedOutboxEvent) -> None:
        if event.tenant_id != self.tenant_id:
            raise RuntimeError("outbox tenant boundary violated")
        await self.service.mark_published(event)


class IngestionHarness:
    def __init__(self, output: Path) -> None:
        base = ApiSettings()
        endpoints = (
            base.database.url.get_secret_value(),
            base.redis.url.get_secret_value(),
            base.object_store.endpoint,
            base.object_store.presign_endpoint,
        )
        if any(
            urlparse(value).hostname not in {"127.0.0.1", "localhost", "::1"} for value in endpoints
        ):
            raise RuntimeError("the ingestion harness requires loopback infrastructure")
        self.run_id = uuid4().hex
        self.output = output
        self.prefix = "presales-ingestion-" + self.run_id + ":"
        self.settings = ApiSettings(
            _env_file=None,
            database=base.database,
            redis=base.redis,
            object_store=base.object_store,
            upload=base.upload,
            auth=AuthSettings(signing_key=SecretStr(uuid4().hex + uuid4().hex)),
            api=ApiServerSettings(cors_origins=["http://127.0.0.1:5173"]),
            model=ModelSettings(),
            embedding=EmbeddingSettings(),
        )
        self.resources = build_foundation_resources(self.settings)
        self.sessions = create_session_factory(self.resources.database_engine)
        self.principals: list[BootstrapResult] = []
        self.fixtures = build_upload_fixtures()
        self.model = UploadedEvidenceModel(self.fixtures)
        self.calls = self.model.calls
        self.worker_context: Any = None
        self.worker_app: Any = None
        self.worker_resources: Any = None
        self.runner: Any = None
        self.publisher: OutboxPublisher | None = None
        self.store: TenantOutboxStore | None = None
        self.publisher_task: asyncio.Task[None] | None = None
        self.publisher_stop = asyncio.Event()
        self.sentinel_id: UUID | None = None
        self.prefix_verified = False
        self.cleanup_receipt: dict[str, Any] | None = None

    async def start(self) -> None:
        existing = [
            key async for key in self.resources.redis_client.scan_iter(match=self.prefix + "*")
        ]
        if existing:
            raise RuntimeError("test Redis namespace already exists")
        self.prefix_verified = True
        for suffix in ("main", "sentinel"):
            slug = "presales-ingestion-" + self.run_id + "-" + suffix
            self.principals.append(
                await bootstrap_principal(
                    settings=self.settings,
                    session_factory=self.sessions,
                    tenant_name="Synthetic ingestion acceptance " + suffix,
                    tenant_slug=slug,
                    email=slug + "@example.invalid",
                    role=MembershipRole.OWNER,
                    quota_bytes=16 * 1024 * 1024,
                )
            )
        other = self.principals[1]
        sentinel = await JobRuntimeService(session_factory=self.sessions).create_job(
            tenant_id=other.tenant_id,
            actor_id=other.actor_id,
            job_type="acceptance.sentinel",
            idempotency_key=self.run_id,
            payload={"boundary": "must_remain_pending"},
        )
        self.sentinel_id = sentinel.job_id
        self.worker_app, self.worker_resources, self.runner = build_consumer_app(
            WorkerSettings(
                _env_file=None,
                database=self.settings.database,
                redis=self.settings.redis,
                object_store=self.settings.object_store,
                embedding=self.settings.embedding,
                model=ModelSettings(),
            ),
            worker_id="presales-ingestion-" + self.run_id,
        )
        self.worker_app.conf.update(
            broker_transport_options={"global_keyprefix": self.prefix, "polling_interval": 0.2},
            worker_enable_remote_control=False,
            worker_send_task_events=False,
        )
        context = start_worker(
            self.worker_app,
            pool="solo",
            concurrency=1,
            perform_ping_check=False,
            shutdown_timeout=20,
            queues=[JOB_QUEUE_NAME],
            loglevel="ERROR",
        )
        await asyncio.to_thread(context.__enter__)
        self.worker_context = context
        self.store = TenantOutboxStore(self.sessions, self.principals[0].tenant_id)
        self.publisher = OutboxPublisher(
            store=self.store,
            dispatcher=CeleryTaskDispatcher(self.worker_app),
            publisher_id="presales-ingestion-" + self.run_id,
            poll_interval_seconds=0.2,
        )
        self.write_json(
            "run-context.json",
            {
                "runId": self.run_id,
                "tenantIds": [str(p.tenant_id) for p in self.principals],
                "actorIds": [str(p.actor_id) for p in self.principals],
                "redisNamespace": self.prefix,
                "redisNamespaceInitiallyEmpty": True,
                "fixtures": {key: item.metadata() for key, item in self.fixtures.items()},
                "boundaries": {
                    "identity": "local JWT + default DatabasePrincipalResolver",
                    "model": "httpx.MockTransport; no external model requests",
                    "embedding": "HashEmbeddingProvider",
                    "ingestion": "browser upload + MinIO + DB Outbox + Redis + Celery",
                },
            },
        )

    def write_json(self, name: str, payload: object) -> None:
        (self.output / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    async def resume(self) -> None:
        if self.publisher is None:
            raise RuntimeError("publisher is not initialized")
        if self.publisher_task is None:
            self.publisher_stop = asyncio.Event()
            self.publisher_task = asyncio.create_task(self.publisher.run(self.publisher_stop))

    async def pause(self) -> None:
        self.publisher_stop.set()
        if self.publisher_task is not None:
            await self.publisher_task
            self.publisher_task = None

    async def model_response(self, request: httpx.Request) -> httpx.Response:
        return await self.model.respond(request)

    def presales_service(self) -> PresalesService:
        model = ModelSettings(
            provider=ModelProvider.OPENAI_COMPATIBLE,
            base_url="https://presales-ingestion.invalid/v1",
            api_key=SecretStr("test-only"),
            model_name="controlled-ingestion-fixture",
        )
        return PresalesService(
            session_factory=self.sessions,
            retriever=HybridRetrievalService(
                session_factory=self.sessions, embedding_provider=HashEmbeddingProvider()
            ),
            gateway=OpenAICompatiblePresalesGateway(
                model, transport=httpx.MockTransport(self.model_response)
            ),
            settings=PresalesSettings(generation_enabled=True),
        )

    async def snapshot(self) -> dict[str, Any]:
        tenant_id = self.principals[0].tenant_id
        async with self.sessions() as session:
            uploads = (
                await session.scalars(
                    select(UploadSession).where(UploadSession.tenant_id == tenant_id)
                )
            ).all()
            versions = (
                await session.scalars(
                    select(DocumentVersion).where(DocumentVersion.tenant_id == tenant_id)
                )
            ).all()
            generations = (
                await session.scalars(
                    select(DocumentIngestionGeneration).where(
                        DocumentIngestionGeneration.tenant_id == tenant_id
                    )
                )
            ).all()
            chunks = (
                await session.scalars(
                    select(DocumentChunk).where(DocumentChunk.tenant_id == tenant_id)
                )
            ).all()
            jobs = (await session.scalars(select(Job).where(Job.tenant_id == tenant_id))).all()
            attempts = (
                await session.scalars(select(JobAttempt).where(JobAttempt.tenant_id == tenant_id))
            ).all()
            events = (
                await session.scalars(select(OutboxEvent).where(OutboxEvent.tenant_id == tenant_id))
            ).all()
            sentinel = await session.get(Job, self.sentinel_id)
            sentinel_event = await session.scalar(
                select(OutboxEvent).where(OutboxEvent.aggregate_id == self.sentinel_id)
            )
            assert sentinel is not None and sentinel_event is not None
            sentinel_status = {
                "jobId": str(sentinel.id),
                "jobStatus": sentinel.status,
                "attempts": sentinel.attempts,
                "outboxStatus": sentinel_event.status,
                "outboxAttempts": sentinel_event.attempts,
            }
        objects = {}
        for version in versions:
            content = await self.resources.multipart_object_store.get_range(
                bucket=self.settings.object_store.documents_bucket,
                key=version.object_key,
                start=0,
                end_inclusive=version.size_bytes - 1,
            )
            objects[str(version.id)] = hashlib.sha256(content).hexdigest()
        return {
            "runId": self.run_id,
            "publisherRunning": self.publisher_task is not None,
            "uploads": [
                {
                    "id": str(u.id),
                    "filename": u.original_filename,
                    "status": u.status,
                    "sha256": u.declared_sha256,
                    "sizeBytes": u.size_bytes,
                    "versionId": str(u.document_version_id),
                }
                for u in uploads
            ],
            "versions": [
                {
                    "id": str(v.id),
                    "documentId": str(v.document_id),
                    "filename": v.original_filename,
                    "status": v.status,
                    "declaredSha256": v.declared_sha256,
                    "objectSha256": objects[str(v.id)],
                    "contentSha256Verified": v.content_sha256_verified_at is not None,
                }
                for v in versions
            ],
            "generations": [
                {
                    "id": str(g.id),
                    "versionId": str(g.document_version_id),
                    "status": g.status,
                    "stage": g.stage,
                    "active": g.active,
                    "chunkCount": g.chunk_count,
                    "embeddedCount": g.embedded_count,
                    "errorCode": g.error_code,
                    "embeddingModel": g.embedding_model,
                }
                for g in generations
            ],
            "chunks": [
                {
                    "id": str(c.id),
                    "versionId": str(c.document_version_id),
                    "generationId": str(c.generation_id),
                    "text": c.normalized_text,
                    "heading": c.heading,
                    "pageNumber": c.page_number,
                    "startOffset": c.start_offset,
                    "endOffset": c.end_offset,
                    "sha256": c.content_sha256,
                    "embeddingPresent": c.embedding is not None,
                }
                for c in chunks
            ],
            "jobs": [
                {
                    "id": str(j.id),
                    "versionId": str(j.document_version_id),
                    "status": j.status,
                    "attempts": j.attempts,
                    "errorCode": j.last_error_code,
                }
                for j in jobs
            ],
            "attempts": [
                {
                    "jobId": str(a.job_id),
                    "status": a.status,
                    "workerId": a.worker_id,
                    "errorCode": a.error_code,
                }
                for a in attempts
            ],
            "outbox": [
                {
                    "id": str(e.id),
                    "jobId": str(e.aggregate_id),
                    "status": e.status,
                    "attempts": e.attempts,
                }
                for e in events
            ],
            "claimedEventIds": self.store.claimed_ids if self.store else [],
            "mockModelRequests": self.calls,
            "sentinel": sentinel_status,
            "redisNamespace": self.prefix,
        }

    async def cleanup(self) -> dict[str, Any]:
        if self.cleanup_receipt is not None:
            return self.cleanup_receipt
        receipt: dict[str, Any] = {"runId": self.run_id, "success": False}
        try:
            await self.pause()
            if self.worker_context is not None:
                await asyncio.to_thread(self.worker_context.__exit__, None, None, None)
                self.worker_context = None
            receipt["workerStopped"] = True
            if self.runner is not None:
                await asyncio.to_thread(self.runner.run, self.worker_resources.close())
                await asyncio.to_thread(self.runner.close)
                self.runner = None
            if self.worker_app is not None:
                await asyncio.to_thread(self.worker_app.close)
                self.worker_app = None
            tenant_ids = [p.tenant_id for p in self.principals]
            actor_ids = [p.actor_id for p in self.principals]
            async with self.sessions() as session:
                uploads = (
                    await session.scalars(
                        select(UploadSession).where(UploadSession.tenant_id.in_(tenant_ids))
                    )
                ).all()
            store, bucket = (
                self.resources.multipart_object_store,
                self.settings.object_store.documents_bucket,
            )
            aborted = 0
            for upload in uploads:
                pending = await store.list_incomplete_uploads(
                    bucket=bucket, prefix=upload.object_key
                )
                for item in pending:
                    if (
                        item.key == upload.object_key
                        and item.upload_id == upload.object_store_upload_id
                    ):
                        await store.abort_upload(
                            bucket=bucket, key=item.key, upload_id=item.upload_id
                        )
                        aborted += 1
                await store.delete_object(bucket=bucket, key=upload.object_key)
            remaining_objects = 0
            remaining_multipart = 0
            for upload in uploads:
                listing = await asyncio.to_thread(
                    self.resources.object_store_client.list_objects_v2,
                    Bucket=bucket,
                    Prefix=upload.object_key,
                )
                remaining_objects += sum(
                    item["Key"] == upload.object_key for item in listing.get("Contents", [])
                )
                pending = await store.list_incomplete_uploads(
                    bucket=bucket, prefix=upload.object_key
                )
                remaining_multipart += sum(item.key == upload.object_key for item in pending)
            async with self.sessions.begin() as session:
                await session.execute(
                    update(UploadSession)
                    .where(UploadSession.tenant_id.in_(tenant_ids))
                    .values(document_version_id=None)
                )
                await session.execute(delete(Document).where(Document.tenant_id.in_(tenant_ids)))
                await session.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
                await session.execute(delete(User).where(User.id.in_(actor_ids)))
                remaining_tenants = await session.scalar(
                    select(func.count()).select_from(Tenant).where(Tenant.id.in_(tenant_ids))
                )
                remaining_users = await session.scalar(
                    select(func.count()).select_from(User).where(User.id.in_(actor_ids))
                )
            redis_keys: list[str] = []
            if self.prefix_verified:
                redis_keys = [
                    key
                    async for key in self.resources.redis_client.scan_iter(match=self.prefix + "*")
                ]
                if any(not key.startswith(self.prefix) for key in redis_keys):
                    raise RuntimeError("Redis cleanup escaped test namespace")
                if redis_keys:
                    await self.resources.redis_client.delete(*redis_keys)
            remaining_redis = [
                key async for key in self.resources.redis_client.scan_iter(match=self.prefix + "*")
            ]
            receipt.update(
                {
                    "objectsDeleted": len(uploads),
                    "multipartAborted": aborted,
                    "remainingObjects": remaining_objects,
                    "remainingMultipartUploads": remaining_multipart,
                    "remainingTestTenants": remaining_tenants or 0,
                    "remainingTestUsers": remaining_users or 0,
                    "redisKeysDeleted": redis_keys,
                    "remainingRedisKeys": len(remaining_redis),
                }
            )
            receipt["success"] = not any(
                (
                    remaining_objects,
                    remaining_multipart,
                    remaining_tenants,
                    remaining_users,
                    remaining_redis,
                )
            )
            if not receipt["success"]:
                raise RuntimeError("test resources remain after cleanup")
            self.cleanup_receipt = receipt
            return receipt
        except Exception as error:
            receipt["errorType"] = type(error).__name__
            raise
        finally:
            self.write_json("cleanup.json", receipt)


async def main(output: Path) -> None:
    harness = IngestionHarness(output)
    try:
        await harness.start()
        app = create_app(settings=harness.settings, presales_service=harness.presales_service())
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=18766, access_log=False, log_level="warning")
        )

        def require_header(value: str | None) -> None:
            if value != "presales-ingestion":
                raise HTTPException(403)

        @app.get("/__ingestion_test__/context")
        async def context(x_presales_test: str | None = Header(default=None)) -> dict:
            require_header(x_presales_test)
            first, other = harness.principals
            return {
                "token": first.token,
                "otherToken": other.token,
                "tenantId": str(first.tenant_id),
                "actorId": str(first.actor_id),
                "fixtures": {key: item.metadata() for key, item in harness.fixtures.items()},
            }

        @app.get("/__ingestion_test__/files/{name}")
        async def fixture_file(
            name: str, x_presales_test: str | None = Header(default=None)
        ) -> Response:
            require_header(x_presales_test)
            if name not in harness.fixtures:
                raise HTTPException(404)
            fixture = harness.fixtures[name]
            return Response(content=fixture.content, media_type=fixture.media_type)

        @app.post("/__ingestion_test__/publisher/{action}")
        async def publishing(
            action: str, x_presales_test: str | None = Header(default=None)
        ) -> dict:
            require_header(x_presales_test)
            if action == "resume":
                await harness.resume()
            elif action == "pause":
                await harness.pause()
            else:
                raise HTTPException(404)
            return {"publisherRunning": harness.publisher_task is not None}

        @app.get("/__ingestion_test__/snapshot")
        async def snapshot(x_presales_test: str | None = Header(default=None)) -> dict:
            require_header(x_presales_test)
            return await harness.snapshot()

        @app.post("/__ingestion_test__/shutdown")
        async def shutdown(x_presales_test: str | None = Header(default=None)) -> dict:
            require_header(x_presales_test)
            try:
                result = await harness.cleanup()
            finally:
                server.should_exit = True
            return result

        await server.serve()
    finally:
        try:
            await harness.cleanup()
        finally:
            await harness.resources.close()


if __name__ == "__main__":
    output_directory = Path(os.environ["PRESALES_INGESTION_OUTPUT_DIR"]).resolve(strict=True)
    asyncio.run(main(output_directory), loop_factory=selector_event_loop_factory)
