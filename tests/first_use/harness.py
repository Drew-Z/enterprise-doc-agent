"""Real first-use services in an owned schema, with synthetic HTTP providers."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from celery import Celery
from celery.contrib.testing.worker import start_worker
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.schema import CreateSchema, DropSchema

from enterprise_doc_api.browser_auth.settings import BrowserAuthSettings
from enterprise_doc_core.admission.contracts import (
    AdmissionGrantRequest,
    PlatformAdmissionOperator,
    prepare_admission_credential,
)
from enterprise_doc_core.admission.models import TenantAdmissionGrant
from enterprise_doc_core.admission.service import TenantAdmissionService
from enterprise_doc_core.billing.administration import EntitlementAdministrationService
from enterprise_doc_core.billing.administration_contracts import (
    EntitlementConfiguration,
    PlatformEntitlementOperator,
)
from enterprise_doc_core.config import EmbeddingSettings, ModelProvider, ModelSettings
from enterprise_doc_core.db.metadata import metadata
from enterprise_doc_core.health import FoundationResources, build_foundation_resources
from enterprise_doc_core.identity.models import Tenant
from enterprise_doc_core.presales.settings import PresalesSettings
from enterprise_doc_worker.config import WorkerSettings
from enterprise_doc_worker.consumer_main import build_consumer_app
from enterprise_doc_worker.publisher import OutboxPublisher
from enterprise_doc_worker.queue import JOB_QUEUE_NAME, AsyncTaskRunner, CeleryTaskDispatcher
from tests.invitations.browser_server import ACCOUNTS, IDP_ORIGIN, WEB_ORIGIN, InvitationHarness
from tests.presales.ingestion_fixtures import build_upload_fixtures
from tests.presales.ingestion_server import TenantOutboxStore
from tests.presales.model_fixture import UploadedEvidenceModel


async def public_snapshot(engine: AsyncEngine) -> dict[str, dict[str, object]]:
    """Hash public rows without persisting their values or changing their state."""
    result: dict[str, dict[str, object]] = {}
    async with engine.begin() as connection:
        await connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        names = list(
            await connection.scalars(
                text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
            )
        )
        for name in names:
            quoted = connection.dialect.identifier_preparer.quote(name)
            rows = list(
                await connection.scalars(
                    text(
                        f"SELECT row_to_json(record)::text FROM public.{quoted} AS record "
                        "ORDER BY row_to_json(record)::text"
                    )
                )
            )
            result[name] = {
                "rows": len(rows),
                "sha256": hashlib.sha256("\n".join(rows).encode()).hexdigest(),
            }
    return result


class FirstUseHarness(InvitationHarness):
    def __init__(self, output: Path, *, keycloak_auth: BrowserAuthSettings | None = None) -> None:
        super().__init__(output)
        self.identity_provider = "signed"
        if keycloak_auth is not None:
            keycloak_auth.validate_environment(self.settings.app_env)
            self.settings = self.settings.model_copy(update={"browser_auth": keycloak_auth})
            self.identity_provider = "keycloak"
        self.issuer = self.settings.browser_auth.issuer
        assert self.issuer is not None
        if any(
            key in self.admin.url.query for key in ("host", "hostaddr", "service", "servicefile")
        ):
            raise RuntimeError("Database endpoint overrides are not allowed in this harness.")
        self.model_key = SecretStr(secrets.token_urlsafe(32))
        self.settings = self.settings.model_copy(
            update={
                "model": ModelSettings(
                    provider=ModelProvider.OPENAI_COMPATIBLE,
                    base_url=IDP_ORIGIN + "/model/v1",
                    api_key=self.model_key,
                    model_name="controlled-first-use",
                ),
                "embedding": EmbeddingSettings(),
                "presales": PresalesSettings(generation_enabled=True),
            }
        )
        self.resources = build_foundation_resources(self.settings)
        self.fixtures = build_upload_fixtures()
        self.model = UploadedEvidenceModel(self.fixtures, model_name="controlled-first-use")
        self.model_requests: list[dict[str, object]] = []
        self.fail_next_model = False
        self.grant_ids: set[UUID] = set()
        self.admission_codes: dict[str, SecretStr] = {}
        self.configurations: dict[UUID, EntitlementConfiguration] = {}
        self.prefix = "first-use-" + self.run_id + ":"
        self.prefix_verified = False
        self.worker_context: AbstractContextManager[object] | None = None
        self.worker_app: Celery | None = None
        self.worker_resources: FoundationResources | None = None
        self.runner: AsyncTaskRunner | None = None
        self.stores: dict[UUID, TenantOutboxStore] = {}
        self.publishers: dict[UUID, OutboxPublisher] = {}
        self.publisher_tasks: list[asyncio.Task[None]] = []
        self.publisher_stop = asyncio.Event()
        self.public_before: dict[str, dict[str, object]] | None = None
        self.cleanup_receipt: dict[str, object] | None = None

    async def start(self) -> None:
        self.public_before = await public_snapshot(self.admin)
        async with self.admin.begin() as connection:
            await connection.execute(CreateSchema(self.schema))
            self.created_schema = True
        async with self.engine.begin() as connection:
            assert await connection.scalar(text("SELECT current_schema()")) == self.schema
            await connection.run_sync(lambda sync: metadata.create_all(sync, checkfirst=False))
            names = set(
                await connection.scalars(
                    text("SELECT tablename FROM pg_tables WHERE schemaname=current_schema()")
                )
            )
            assert names == set(metadata.tables)
            self.migrated = True
        existing = [
            key async for key in self.resources.redis_client.scan_iter(match=self.prefix + "*")
        ]
        if existing:
            raise RuntimeError("The new Redis namespace is not empty.")
        self.prefix_verified = True
        admission = TenantAdmissionService(
            session_factory=self.sessions, trusted_issuers=frozenset({self.issuer})
        )
        for key in ("a", "b"):
            prepared = prepare_admission_credential()
            self.grant_ids.add(prepared.grant_id)
            self.admission_codes[key] = prepared.token
            await admission.issue(
                operator=PlatformAdmissionOperator(
                    "first-use-fixture", "Synthetic browser admission acceptance"
                ),
                request=AdmissionGrantRequest(
                    recipient_email=ACCOUNTS["owner"][1],
                    issuer=self.issuer,
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                    quota_bytes=32 * 1024 * 1024,
                    seat_limit=2,
                ),
                credential=prepared,
            )
        assert not await self.owned_tenants()
        self.worker_app, self.worker_resources, self.runner = build_consumer_app(
            WorkerSettings(
                _env_file=None,
                app_env=self.settings.app_env,
                database=self.settings.database,
                redis=self.settings.redis,
                object_store=self.settings.object_store,
                embedding=self.settings.embedding,
                model=ModelSettings(),
            ),
            worker_id="first-use-" + self.run_id,
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
        self.write(
            "run-context.json",
            {
                "runId": self.run_id,
                "schema": self.schema,
                "webOrigin": WEB_ORIGIN,
                "apiPort": 18769,
                "idpOrigin": self.issuer,
                "identityProvider": self.identity_provider,
                "redisNamespace": self.prefix,
                "redisNamespaceInitiallyEmpty": True,
                "tenantCountAtStart": 0,
                "admissionGrantCount": len(self.grant_ids),
                "fixtures": {key: item.metadata() for key, item in self.fixtures.items()},
                "boundaries": {
                    "identity": (
                        "real local Keycloak; SMTP to in-memory HTTP capture"
                        if self.identity_provider == "keycloak"
                        else "synthetic signed loopback HTTP IdP with real code/PKCE"
                    ),
                    "api": "production create_app; no injected business services or resolver",
                    "ingestion": "real browser/hash/MinIO/Outbox/Redis/Celery/parser",
                    "model": "controlled loopback HTTP; no external model requests",
                    "embedding": "HashEmbeddingProvider",
                    "commercial": "fixture-owned local periods only; no customers or payments",
                },
            },
        )

    async def owned_tenants(self) -> tuple[UUID, ...]:
        async with self.sessions() as session:
            assert await session.scalar(text("SELECT current_schema()")) == self.schema
            grants = (
                await session.scalars(
                    select(TenantAdmissionGrant).where(TenantAdmissionGrant.id.in_(self.grant_ids))
                )
            ).all()
            ids = tuple(grant.accepted_tenant_id for grant in grants if grant.accepted_tenant_id)
            actual = set(await session.scalars(select(Tenant.id)))
        if actual != set(ids):
            raise RuntimeError("A tenant did not originate from this run's browser admission.")
        return ids

    async def configure(self, tenant_id: UUID, request_limit: int) -> dict[str, object]:
        if tenant_id not in await self.owned_tenants():
            raise ValueError("Unknown first-use tenant.")
        configuration = self.configurations.get(tenant_id)
        if configuration is None:
            now = datetime.now(UTC)
            configuration = EntitlementConfiguration(
                entitlement_id=uuid4(),
                expected_version=0,
                plan_code="synthetic-first-use",
                period_start=now - timedelta(minutes=1),
                period_end=now + timedelta(hours=1),
                provider_request_limit=request_limit,
            )
            self.configurations[tenant_id] = configuration
        elif configuration.provider_request_limit != request_limit:
            raise ValueError("A test period cannot be rewritten.")
        result = await EntitlementAdministrationService(session_factory=self.sessions).configure(
            tenant_id=tenant_id,
            operator=PlatformEntitlementOperator(
                "first-use-fixture", "Synthetic browser quota acceptance"
            ),
            configuration=configuration,
        )
        return {
            "tenantId": str(tenant_id),
            "entitlementId": str(result.entitlement.entitlement_id),
            "requestLimit": request_limit,
            "replayed": result.replayed,
        }

    async def resume(self) -> None:
        if self.worker_app is None:
            raise RuntimeError("Worker not started.")
        ids = await self.owned_tenants()
        if not ids:
            raise RuntimeError("Admit a tenant before starting its publisher.")
        if self.publisher_tasks:
            return
        for tenant_id in ids:
            if tenant_id not in self.stores:
                store = TenantOutboxStore(self.sessions, tenant_id)
                self.stores[tenant_id] = store
                self.publishers[tenant_id] = OutboxPublisher(
                    store=store,
                    dispatcher=CeleryTaskDispatcher(self.worker_app),
                    publisher_id="first-use-" + self.run_id + "-" + str(tenant_id),
                    poll_interval_seconds=0.2,
                )
        self.publisher_stop = asyncio.Event()
        self.publisher_tasks = [
            asyncio.create_task(publisher.run(self.publisher_stop))
            for publisher in self.publishers.values()
        ]

    async def pause(self) -> None:
        self.publisher_stop.set()
        if self.publisher_tasks:
            await asyncio.gather(*self.publisher_tasks)
            self.publisher_tasks = []

    async def stop_worker(self) -> None:
        await self.pause()
        if self.worker_context is not None:
            await asyncio.to_thread(self.worker_context.__exit__, None, None, None)
            self.worker_context = None
        if self.runner is not None and self.worker_resources is not None:
            await asyncio.to_thread(self.runner.run, self.worker_resources.close())
            await asyncio.to_thread(self.runner.close)
            self.runner = None
        if self.worker_app is not None:
            await asyncio.to_thread(self.worker_app.close)
            self.worker_app = None

    async def snapshot(self) -> dict[str, Any]:
        from tests.first_use.evidence import pipeline_snapshot

        result = await super().snapshot()
        result.update(await pipeline_snapshot(self))
        return result

    async def remove_objects(self) -> dict[str, object]:
        from enterprise_doc_core.uploads.models import UploadSession
        from enterprise_doc_core.uploads.policy import build_object_key

        tenant_ids = set(await self.owned_tenants())
        async with self.sessions() as session:
            assert await session.scalar(text("SELECT current_schema()")) == self.schema
            uploads = (await session.scalars(select(UploadSession))).all()
        store = self.resources.multipart_object_store
        bucket = self.settings.object_store.documents_bucket
        keys: list[str] = []
        aborted = remaining_objects = remaining_multipart = 0
        for upload in uploads:
            expected = build_object_key(session_id=upload.id, version_id=upload.pending_version_id)
            if upload.tenant_id not in tenant_ids or upload.object_key != expected:
                raise RuntimeError("Upload object ownership check failed.")
            keys.append(expected)
            for item in await store.list_incomplete_uploads(bucket=bucket, prefix=expected):
                if item.key == expected and item.upload_id == upload.object_store_upload_id:
                    await store.abort_upload(bucket=bucket, key=expected, upload_id=item.upload_id)
                    aborted += 1
            await store.delete_object(bucket=bucket, key=expected)
            listing = await asyncio.to_thread(
                self.resources.object_store_client.list_objects_v2, Bucket=bucket, Prefix=expected
            )
            remaining_objects += sum(
                item["Key"] == expected for item in listing.get("Contents", [])
            )
            remaining_multipart += sum(
                item.key == expected
                for item in await store.list_incomplete_uploads(bucket=bucket, prefix=expected)
            )
        if remaining_objects or remaining_multipart:
            raise RuntimeError("Owned object-store resources remain.")
        return {
            "objectKeys": keys,
            "objectKeysChecked": len(keys),
            "multipartAborted": aborted,
            "remainingObjects": remaining_objects,
            "remainingMultipartUploads": remaining_multipart,
        }

    async def cleanup(self) -> None:
        if self.cleanup_receipt is not None:
            return
        receipt: dict[str, object] = {
            "runId": self.run_id,
            "schema": self.schema,
            "success": False,
            "schemaRemoved": False,
            "workerStopped": False,
            "publisherStopped": False,
            "externalModelRequests": 0,
            "emailsSent": 0,
        }
        try:
            await self.stop_worker()
            receipt.update(workerStopped=True, publisherStopped=True)
            if self.migrated:
                self.write("database-evidence.json", await self.snapshot())
                receipt.update(await self.remove_objects())
            redis_keys: list[str] = []
            if self.prefix_verified:
                redis_keys = [
                    key
                    async for key in self.resources.redis_client.scan_iter(match=self.prefix + "*")
                ]
                if any(not key.startswith(self.prefix) for key in redis_keys):
                    raise RuntimeError("Redis cleanup escaped this run's namespace.")
                if redis_keys:
                    await self.resources.redis_client.delete(*redis_keys)
            remaining_redis = [
                key async for key in self.resources.redis_client.scan_iter(match=self.prefix + "*")
            ]
            receipt.update(
                redisKeysDeleted=redis_keys,
                remainingRedisKeys=len(remaining_redis),
                redisNamespace=self.prefix,
            )
            if remaining_redis:
                raise RuntimeError("Owned Redis resources remain.")
            await self.resources.close()
            await self.engine.dispose()
            if self.created_schema:
                if self.schema != "invitation_e2e_" + self.run_id:
                    raise RuntimeError("Unexpected first-use schema.")
                async with self.admin.begin() as connection:
                    await connection.execute(DropSchema(self.schema, cascade=True))
                async with self.admin.connect() as connection:
                    assert not await connection.scalar(
                        text("SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=:schema)"),
                        {"schema": self.schema},
                    )
                receipt["schemaRemoved"] = True
            if self.public_before is not None:
                after = await public_snapshot(self.admin)
                changed = sorted(
                    name
                    for name in self.public_before.keys() | after.keys()
                    if self.public_before.get(name) != after.get(name)
                )
                self.write(
                    "public-database-verification.json",
                    {
                        "mode": "read_only_repeatable_read",
                        "unchanged": not changed,
                        "changedTables": changed,
                        "before": self.public_before,
                        "after": after,
                    },
                )
                receipt["publicUnchanged"] = not changed
                if changed:
                    raise RuntimeError(
                        "Pre-existing public database rows changed during acceptance."
                    )
            receipt["success"] = True
        except Exception as error:
            receipt["errorType"] = type(error).__name__
            raise
        finally:
            await self.resources.close()
            await self.engine.dispose()
            await self.admin.dispose()
            self.cleanup_receipt = receipt
            self.write("cleanup.json", receipt)
