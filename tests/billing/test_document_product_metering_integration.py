import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, select
from tests.jobs.test_m3_ingestion_integration import (
    FailOnceEmbeddingProvider,
    FakeObjectStore,
    _seed_uploaded_document,
)

from enterprise_doc_core.billing.administration import EntitlementAdministrationService
from enterprise_doc_core.billing.administration_contracts import (
    EntitlementConfiguration,
    PlatformEntitlementOperator,
)
from enterprise_doc_core.billing.product_models import ProductQuota, ProductUsageEvent
from enterprise_doc_core.billing.provider_models import ProviderDispatch
from enterprise_doc_core.config import AppEnvironment, EmbeddingSettings
from enterprise_doc_core.documents import HashEmbeddingProvider
from enterprise_doc_core.documents.embedding_provider import OpenAICompatibleEmbeddingProvider
from enterprise_doc_core.documents.ingestion_service import (
    DocumentIngestionError,
    DocumentIngestionService,
)
from enterprise_doc_core.documents.inventory_service import DocumentInventoryService
from enterprise_doc_core.identity import Membership, Tenant, User
from enterprise_doc_core.jobs import JobRuntimeService, RetryDisposition

pytestmark = pytest.mark.integration


async def test_cancel_during_embedding_response_prevents_next_retry(metered_document):
    sessions, tenant_id, _version_id, runtime, claim, content = metered_document
    now = datetime.now(UTC)
    await EntitlementAdministrationService(session_factory=sessions).configure(
        tenant_id=tenant_id,
        operator=PlatformEntitlementOperator("test", "cancel embedding"),
        configuration=EntitlementConfiguration(
            entitlement_id=uuid4(),
            expected_version=0,
            plan_code="invited",
            period_start=now - timedelta(minutes=1),
            period_end=now + timedelta(days=1),
            provider_request_limit=0,
            document_bytes_limit=len(content),
        ),
    )
    calls = []

    async def provider(request):
        calls.append(request)
        await runtime.cancel(job_id=claim.job_id, tenant_id=tenant_id)
        return httpx.Response(429, headers={"Retry-After": "0"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        service = DocumentIngestionService(
            session_factory=sessions,
            object_store=FakeObjectStore(content),
            documents_bucket="documents",
            app_env=AppEnvironment.PRODUCTION,
            embedding_provider=OpenAICompatibleEmbeddingProvider(
                settings=EmbeddingSettings(
                    provider="openai_compatible",
                    base_url="https://provider.test/v1",
                    api_key="test-only",
                    model_name="embed",
                ),
                client=client,
                require_metering=True,
            ),
        )
        with pytest.raises(DocumentIngestionError) as stale:
            await service(claim)
        assert stale.value.code == "document_execution_stale"
    await runtime.fail(
        claim,
        disposition=RetryDisposition.CANCELLED,
        error_code="cancelled",
        error_message="user cancellation",
    )
    assert len(calls) == 1
    async with sessions() as session:
        quota = await session.scalar(
            select(ProductQuota).where(
                ProductQuota.tenant_id == tenant_id, ProductQuota.metric == "document_bytes"
            )
        )
        assert (quota.units_used, quota.units_reserved) == (0, 0)
        receipts = (
            await session.scalars(
                select(ProviderDispatch).where(ProviderDispatch.tenant_id == tenant_id)
            )
        ).all()
        assert len(receipts) == 1 and receipts[0].estimated_cost is None


@pytest.mark.parametrize("enough", [True, False])
async def test_document_processing_uses_separate_byte_quota_before_reading(
    metered_document, enough
):
    sessions, tenant_id, _version_id, runtime, claim, content = metered_document
    now = datetime.now(UTC)
    await EntitlementAdministrationService(session_factory=sessions).configure(
        tenant_id=tenant_id,
        operator=PlatformEntitlementOperator("ingestion-test", "processing quota"),
        configuration=EntitlementConfiguration(
            entitlement_id=uuid4(),
            expected_version=0,
            plan_code="invited",
            period_start=now - timedelta(minutes=1),
            period_end=now + timedelta(days=1),
            provider_request_limit=0,
            agent_task_limit=0,
            document_bytes_limit=len(content) if enough else 0,
        ),
    )
    store = FakeObjectStore(content)
    service = DocumentIngestionService(
        session_factory=sessions,
        object_store=store,
        documents_bucket="documents",
        embedding_provider=HashEmbeddingProvider(),
        app_env=AppEnvironment.PRODUCTION,
    )
    if enough:
        await service(claim)
        await service(claim)
        assert await runtime.succeed(claim) == "succeeded"
        assert store.head_calls == 1
    else:
        with pytest.raises(DocumentIngestionError) as rejected:
            await service(claim)
        assert rejected.value.code == "document_usage_limit" and not rejected.value.retryable
        assert store.head_calls == 0
        inventory = await DocumentInventoryService(session_factory=sessions).list_versions(
            tenant_id=tenant_id, actor_id=claim.actor_id
        )
        assert inventory[0].ingestion_status == "failed"
        assert inventory[0].error_code == "document_usage_limit"
    async with sessions() as session:
        quota = await session.scalar(
            select(ProductQuota).where(
                ProductQuota.tenant_id == tenant_id, ProductQuota.metric == "document_bytes"
            )
        )
        assert quota.units_used == (len(content) if enough else 0) and quota.units_reserved == 0
        events = (
            await session.scalars(
                select(ProductUsageEvent).where(ProductUsageEvent.tenant_id == tenant_id)
            )
        ).all()
        assert len(events) == (1 if enough else 0)


async def test_terminal_document_failure_releases_and_manual_retry_uses_new_receipt(
    metered_document,
):
    sessions, tenant_id, _version_id, runtime, claim, content = metered_document
    now = datetime.now(UTC)
    await EntitlementAdministrationService(session_factory=sessions).configure(
        tenant_id=tenant_id,
        operator=PlatformEntitlementOperator("ingestion-test", "retry allocation"),
        configuration=EntitlementConfiguration(
            entitlement_id=uuid4(),
            expected_version=0,
            plan_code="invited",
            period_start=now - timedelta(minutes=1),
            period_end=now + timedelta(days=1),
            provider_request_limit=0,
            document_bytes_limit=len(content),
        ),
    )
    provider = FailOnceEmbeddingProvider()
    service = DocumentIngestionService(
        session_factory=sessions,
        object_store=FakeObjectStore(content),
        documents_bucket="documents",
        embedding_provider=provider,
        app_env=AppEnvironment.PRODUCTION,
    )
    with pytest.raises(DocumentIngestionError):
        await service(claim)
    await runtime.fail(
        claim,
        disposition=RetryDisposition.PERMANENT,
        error_code="test_failure",
        error_message="controlled error",
    )
    async with sessions() as session:
        quota = await session.scalar(
            select(ProductQuota).where(
                ProductQuota.tenant_id == tenant_id, ProductQuota.metric == "document_bytes"
            )
        )
        assert (quota.units_used, quota.units_reserved) == (0, 0)
    await runtime.retry_dead(job_id=claim.job_id, tenant_id=tenant_id)
    retried = await runtime.claim(job_id=claim.job_id, worker_id="manual-retry")
    assert retried is not None
    await service(retried)
    await runtime.succeed(retried)
    async with sessions() as session:
        quota = await session.scalar(
            select(ProductQuota).where(
                ProductQuota.tenant_id == tenant_id, ProductQuota.metric == "document_bytes"
            )
        )
        assert (quota.units_used, quota.units_reserved) == (len(content), 0)
        events = (
            await session.scalars(
                select(ProductUsageEvent).where(ProductUsageEvent.tenant_id == tenant_id)
            )
        ).all()
        assert sorted(event.event_type for event in events) == ["consume", "release"]


async def test_two_jobs_for_one_document_do_not_dispatch_twice(metered_document):
    sessions, tenant_id, version_id, runtime, claim, content = metered_document
    now = datetime.now(UTC)
    await EntitlementAdministrationService(session_factory=sessions).configure(
        tenant_id=tenant_id,
        operator=PlatformEntitlementOperator("ingestion-test", "concurrent processing"),
        configuration=EntitlementConfiguration(
            entitlement_id=uuid4(),
            expected_version=0,
            plan_code="invited",
            period_start=now - timedelta(minutes=1),
            period_end=now + timedelta(days=1),
            provider_request_limit=0,
            document_bytes_limit=len(content) * 2,
        ),
    )
    second = await runtime.create_job(
        tenant_id=tenant_id,
        actor_id=claim.actor_id,
        job_type="document.ingest",
        idempotency_key="second-document-job",
        payload={"document_version_id": str(version_id)},
        document_version_id=version_id,
    )
    second_claim = await runtime.claim(job_id=second.job_id, worker_id="second-worker")
    assert second_claim is not None
    started, finish = asyncio.Event(), asyncio.Event()

    class PausedProvider(HashEmbeddingProvider):
        calls = 0

        async def embed(self, texts):
            self.calls += 1
            started.set()
            await asyncio.wait_for(finish.wait(), timeout=15)
            return await super().embed(texts)

    provider = PausedProvider()
    store = FakeObjectStore(content)
    service = DocumentIngestionService(
        session_factory=sessions,
        object_store=store,
        documents_bucket="documents",
        embedding_provider=provider,
        app_env=AppEnvironment.PRODUCTION,
    )
    first = asyncio.create_task(service(claim))
    try:
        await asyncio.wait_for(started.wait(), timeout=15)
        with pytest.raises(DocumentIngestionError) as busy:
            await asyncio.wait_for(service(second_claim), timeout=5)
        assert busy.value.code == "document_processing_busy" and busy.value.retryable
    finally:
        finish.set()
        await first
    await runtime.succeed(claim)
    await service(second_claim)
    await runtime.succeed(second_claim)
    assert provider.calls == store.head_calls == 1
    async with sessions() as session:
        quota = await session.scalar(
            select(ProductQuota).where(
                ProductQuota.tenant_id == tenant_id, ProductQuota.metric == "document_bytes"
            )
        )
        assert (quota.units_used, quota.units_reserved) == (len(content), 0)


@pytest.fixture
async def metered_document(billing_database):
    sessions, _ = billing_database
    content = b"# Terms\nPayment is due within 30 days.\n"
    tenant_id, actor_id, version_id, _ = await _seed_uploaded_document(sessions, content=content)
    async with sessions.begin() as session:
        session.add(Membership(tenant_id=tenant_id, user_id=actor_id, role="owner"))
    runtime = JobRuntimeService(session_factory=sessions, jitter=lambda _: 0)
    try:
        job = await runtime.create_job(
            tenant_id=tenant_id,
            actor_id=actor_id,
            job_type="document.ingest",
            idempotency_key="metered-document",
            payload={"document_version_id": str(version_id)},
            document_version_id=version_id,
        )
        claim = await runtime.claim(job_id=job.job_id, worker_id="ingestion-test")
        assert claim is not None
        yield sessions, tenant_id, version_id, runtime, claim, content
    finally:
        async with sessions.begin() as session:
            await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
            await session.execute(delete(User).where(User.id == actor_id))
