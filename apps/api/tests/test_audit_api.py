from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.audit import (
    AuditArchiveBatchResult,
    AuditArchiveDownloadResult,
    AuditArchiveVerificationResult,
    AuditEventPage,
    AuditEventResult,
    AuditLegalHoldResult,
    AuditRetentionPlan,
    AuditRetentionPolicyResult,
    AuditRetentionPreview,
)
from enterprise_doc_core.context import PrincipalContext


class StubPrincipalResolver:
    def __init__(self, principal: PrincipalContext) -> None:
        self.principal = principal

    async def resolve(self, _: str) -> PrincipalContext:
        return self.principal


class StubAuditService:
    def __init__(self, tenant_id: UUID) -> None:
        self.tenant_id = tenant_id
        self.calls: list[dict[str, object]] = []
        self.event_id = uuid4()

    async def list_events(self, **kwargs: object) -> AuditEventPage:
        self.calls.append(kwargs)
        return AuditEventPage(
            items=(
                AuditEventResult(
                    event_id=self.event_id,
                    tenant_id=self.tenant_id,
                    actor_id=UUID(str(kwargs["actor_id"])) if kwargs.get("actor_id") else None,
                    action="agent_run.finished",
                    resource_type="agent_run",
                    resource_id=uuid4(),
                    occurred_at=datetime(2026, 8, 25, 8, 0, tzinfo=UTC),
                    request_id="req-1",
                    correlation_id="corr-1",
                    metadata={"status": "succeeded"},
                    schema_version=1,
                ),
            ),
            next_cursor="next-page",
        )


class PagedAuditService:
    def __init__(self, pages: list[AuditEventPage]) -> None:
        self.pages = pages
        self.calls: list[dict[str, object]] = []

    async def list_events(self, **kwargs: object) -> AuditEventPage:
        self.calls.append(kwargs)
        return self.pages.pop(0)


class StubAuditGovernanceService:
    def __init__(self, tenant_id: UUID, actor_id: UUID) -> None:
        self.tenant_id = tenant_id
        self.actor_id = actor_id
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.hold_id = uuid4()
        self.now = datetime(2026, 8, 25, 8, 0, tzinfo=UTC)

    async def get_retention_policy(self, **kwargs: object) -> AuditRetentionPolicyResult:
        self.calls.append(("get_policy", kwargs))
        return AuditRetentionPolicyResult(
            tenant_id=self.tenant_id,
            retention_days=365,
            is_enabled=False,
            updated_by=None,
        )

    async def set_retention_policy(self, **kwargs: object) -> AuditRetentionPolicyResult:
        self.calls.append(("set_policy", kwargs))
        return AuditRetentionPolicyResult(
            tenant_id=self.tenant_id,
            retention_days=int(kwargs["retention_days"]),
            is_enabled=bool(kwargs["is_enabled"]),
            updated_by=self.actor_id,
        )

    async def list_legal_holds(self, **kwargs: object) -> tuple[AuditLegalHoldResult, ...]:
        self.calls.append(("list_holds", kwargs))
        return (self._hold(),)

    async def create_legal_hold(self, **kwargs: object) -> AuditLegalHoldResult:
        self.calls.append(("create_hold", kwargs))
        return self._hold()

    async def release_legal_hold(self, **kwargs: object) -> AuditLegalHoldResult:
        self.calls.append(("release_hold", kwargs))
        return self._hold(released_at=self.now + timedelta(hours=1))

    async def retention_preview(self, **kwargs: object) -> AuditRetentionPreview:
        self.calls.append(("preview", kwargs))
        return AuditRetentionPreview(
            cutoff_at=self.now - timedelta(days=365),
            eligible_event_count=7,
            protected_event_count=2,
        )

    async def retention_plan(self, **kwargs: object) -> AuditRetentionPlan:
        self.calls.append(("plan", kwargs))
        return AuditRetentionPlan(
            policy=AuditRetentionPolicyResult(
                tenant_id=self.tenant_id,
                retention_days=365,
                is_enabled=True,
                updated_by=self.actor_id,
            ),
            cutoff_at=self.now - timedelta(days=365),
            eligible_event_count=7,
            protected_event_count=2,
            eligible_event_ids=(self.hold_id,),
            fingerprint="a" * 64,
        )

    async def archive_retention_plan(self, **kwargs: object) -> AuditArchiveBatchResult:
        self.calls.append(("archive", kwargs))
        return AuditArchiveBatchResult(
            batch_id=self.hold_id,
            tenant_id=self.tenant_id,
            cutoff_at=self.now - timedelta(days=365),
            archived_event_count=7,
            fingerprint="a" * 64,
            bucket="audit-archive",
            object_key="audit-archive/example.json",
            content_sha256="b" * 64,
            size_bytes=512,
            created_by=self.actor_id,
        )

    async def list_archive_batches(self, **kwargs: object) -> tuple[AuditArchiveBatchResult, ...]:
        self.calls.append(("archives", kwargs))
        return (await self.archive_retention_plan(),)

    async def verify_archive_batch(self, **kwargs: object) -> AuditArchiveVerificationResult:
        self.calls.append(("verify_archive", kwargs))
        return AuditArchiveVerificationResult(
            batch_id=self.hold_id,
            tenant_id=self.tenant_id,
            verified_at=self.now,
            valid=True,
            expected_sha256="b" * 64,
            actual_sha256="b" * 64,
            expected_size_bytes=512,
            actual_size_bytes=512,
            envelope_valid=True,
            failure_reason=None,
        )

    async def get_archive_download(self, **kwargs: object) -> AuditArchiveDownloadResult:
        self.calls.append(("download_archive", kwargs))
        return AuditArchiveDownloadResult(
            batch_id=self.hold_id,
            tenant_id=self.tenant_id,
            bucket="audit-archive",
            object_key="audit-archive/example.json",
            content_sha256="b" * 64,
            size_bytes=512,
            url="https://archive.test/example.json?ttl=120",
            expires_in_seconds=120,
        )

    def _hold(self, *, released_at: datetime | None = None) -> AuditLegalHoldResult:
        return AuditLegalHoldResult(
            hold_id=self.hold_id,
            tenant_id=self.tenant_id,
            name="Case 2026-08",
            reason="Preserve contract review evidence",
            resource_type="document",
            resource_id=uuid4(),
            starts_at=self.now,
            expires_at=None,
            released_at=released_at,
            created_by=self.actor_id,
            released_by=self.actor_id if released_at else None,
        )


async def test_audit_api_is_authenticated_and_tenant_scoped() -> None:
    tenant_id = uuid4()
    actor_id = uuid4()
    principal = PrincipalContext(tenant_id=str(tenant_id), actor_id=str(actor_id), role="owner")
    service = StubAuditService(tenant_id)
    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(principal),
        audit_service=service,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        unauthorized = await client.get("/api/audit-events")
        response = await client.get(
            "/api/audit-events?limit=20&action=agent_run.finished&resourceType=agent_run",
            headers={"Authorization": "Bearer token"},
        )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert service.calls[0]["tenant_id"] == tenant_id
    assert service.calls[0]["limit"] == 20
    assert response.json()["items"][0]["action"] == "agent_run.finished"
    assert response.json()["nextCursor"] == "next-page"


async def test_audit_api_rejects_invalid_time_window_before_service_call() -> None:
    principal = PrincipalContext(tenant_id=str(uuid4()), actor_id=str(uuid4()), role="owner")
    service = StubAuditService(UUID(principal.tenant_id))
    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(principal),
        audit_service=service,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/audit-events?from=2026-08-26T00:00:00Z&to=2026-08-25T00:00:00Z",
            headers={"Authorization": "Bearer token"},
        )

    assert response.status_code == 422
    assert service.calls == []


async def test_audit_export_is_authenticated_filtered_and_csv_encoded() -> None:
    tenant_id = uuid4()
    principal = PrincipalContext(tenant_id=str(tenant_id), actor_id=str(uuid4()), role="owner")
    service = StubAuditService(tenant_id)
    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(principal),
        audit_service=service,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/audit-events/export.csv?limit=1&action=agent_run.finished&resourceType=agent_run",
            headers={"Authorization": "Bearer token"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == 'attachment; filename="audit-events.csv"'
    assert response.text.startswith("\ufeffevent_id,tenant_id,actor_id,action")
    assert "agent_run.finished" in response.text
    assert service.calls[0] == {
        "tenant_id": tenant_id,
        "limit": 1,
        "cursor": None,
        "from_date": None,
        "to_date": None,
        "action": "agent_run.finished",
        "resource_type": "agent_run",
        "resource_id": None,
        "actor_id": None,
    }


async def test_audit_export_requires_tenant_owner() -> None:
    tenant_id = uuid4()
    principal = PrincipalContext(tenant_id=str(tenant_id), actor_id=str(uuid4()), role="member")
    service = StubAuditService(tenant_id)
    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(principal),
        audit_service=service,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/audit-events/export.csv",
            headers={"Authorization": "Bearer token"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "audit_export_forbidden"
    assert service.calls == []


async def test_audit_export_follows_cursor_across_pages() -> None:
    tenant_id = uuid4()
    principal = PrincipalContext(tenant_id=str(tenant_id), actor_id=str(uuid4()), role="owner")
    first_event = AuditEventResult(
        event_id=uuid4(),
        tenant_id=tenant_id,
        actor_id=None,
        action="document.upload_completed",
        resource_type="document",
        resource_id=uuid4(),
        occurred_at=datetime(2026, 8, 25, 8, 0, tzinfo=UTC),
        request_id="req-1",
        correlation_id="corr-1",
        metadata={"filename": "first.pdf"},
        schema_version=1,
    )
    second_event = AuditEventResult(
        event_id=uuid4(),
        tenant_id=tenant_id,
        actor_id=None,
        action=first_event.action,
        resource_type=first_event.resource_type,
        resource_id=first_event.resource_id,
        occurred_at=first_event.occurred_at,
        request_id="req-2",
        correlation_id="corr-2",
        metadata={"filename": "second.pdf"},
        schema_version=first_event.schema_version,
    )
    service = PagedAuditService(
        [
            AuditEventPage(items=(first_event,), next_cursor="page-2"),
            AuditEventPage(items=(second_event,), next_cursor=None),
        ],
    )
    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(principal),
        audit_service=service,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/audit-events/export.csv?limit=2",
            headers={"Authorization": "Bearer token"},
        )

    assert response.status_code == 200
    assert "first.pdf" in response.text
    assert "second.pdf" in response.text
    assert [call["limit"] for call in service.calls] == [2, 1]
    assert [call["cursor"] for call in service.calls] == [None, "page-2"]


async def test_audit_governance_owner_can_manage_tenant_policy_and_holds() -> None:
    tenant_id = uuid4()
    actor_id = uuid4()
    resource_id = uuid4()
    principal = PrincipalContext(tenant_id=str(tenant_id), actor_id=str(actor_id), role="owner")
    service = StubAuditGovernanceService(tenant_id, actor_id)
    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(principal),
        audit_governance_service=service,
    )
    headers = {"Authorization": "Bearer token"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        policy = await client.get("/api/audit-governance/retention-policy", headers=headers)
        updated = await client.put(
            "/api/audit-governance/retention-policy",
            headers=headers,
            json={"retentionDays": 730, "isEnabled": True},
        )
        created = await client.post(
            "/api/audit-governance/legal-holds",
            headers=headers,
            json={
                "name": "Case 2026-08",
                "reason": "Preserve contract review evidence",
                "resourceType": "document",
                "resourceId": str(resource_id),
            },
        )
        holds = await client.get("/api/audit-governance/legal-holds", headers=headers)
        preview = await client.get("/api/audit-governance/retention-preview", headers=headers)
        plan = await client.get("/api/audit-governance/retention-plan?limit=25", headers=headers)
        archived = await client.post(
            "/api/audit-governance/retention-archive?limit=25", headers=headers
        )
        archives = await client.get(
            "/api/audit-governance/retention-archives?limit=10", headers=headers
        )
        verified = await client.post(
            f"/api/audit-governance/retention-archives/{service.hold_id}/verify", headers=headers
        )
        download = await client.get(
            f"/api/audit-governance/retention-archives/{service.hold_id}/download?expiresIn=120",
            headers=headers,
        )
        released = await client.delete(
            f"/api/audit-governance/legal-holds/{service.hold_id}", headers=headers
        )

    assert policy.status_code == 200
    assert policy.json()["retentionDays"] == 365
    assert updated.status_code == 200
    assert updated.json()["isEnabled"] is True
    assert created.status_code == 201
    assert created.json()["holdId"] == str(service.hold_id)
    assert holds.status_code == 200
    assert len(holds.json()) == 1
    assert preview.status_code == 200
    assert preview.json()["eligibleEventCount"] == 7
    assert preview.json()["protectedEventCount"] == 2
    assert plan.status_code == 200
    assert plan.json()["eligibleEventCount"] == 7
    assert plan.json()["eligibleEventIds"] == [str(service.hold_id)]
    assert plan.json()["fingerprint"] == "a" * 64
    assert archived.status_code == 201
    assert archived.json()["archivedEventCount"] == 7
    assert archives.status_code == 200
    assert len(archives.json()) == 1
    assert verified.status_code == 200
    assert verified.json()["valid"] is True
    assert download.status_code == 200
    assert download.json()["expiresInSeconds"] == 120
    assert released.status_code == 200
    assert released.json()["releasedAt"] is not None
    assert {name for name, _ in service.calls} == {
        "get_policy",
        "set_policy",
        "create_hold",
        "list_holds",
        "preview",
        "plan",
        "archive",
        "archives",
        "verify_archive",
        "download_archive",
        "release_hold",
    }
    create_call = next(kwargs for name, kwargs in service.calls if name == "create_hold")
    assert create_call["tenant_id"] == tenant_id
    assert create_call["actor_id"] == actor_id
    assert create_call["resource_id"] == resource_id


async def test_audit_governance_is_owner_only_and_validates_payloads() -> None:
    tenant_id = uuid4()
    actor_id = uuid4()
    principal = PrincipalContext(tenant_id=str(tenant_id), actor_id=str(actor_id), role="member")
    service = StubAuditGovernanceService(tenant_id, actor_id)
    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(principal),
        audit_governance_service=service,
    )
    headers = {"Authorization": "Bearer token"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        forbidden = await client.get("/api/audit-governance/legal-holds", headers=headers)
        plan_forbidden = await client.get("/api/audit-governance/retention-plan", headers=headers)
        invalid = await client.put(
            "/api/audit-governance/retention-policy",
            headers=headers,
            json={"retentionDays": 29, "isEnabled": True},
        )

    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "audit_governance_forbidden"
    assert plan_forbidden.status_code == 403
    assert plan_forbidden.json()["error"]["code"] == "audit_governance_forbidden"
    assert invalid.status_code == 422
    assert service.calls == []
