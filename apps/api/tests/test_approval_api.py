from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.agents import (
    ApprovalDecisionResult,
    ApprovalPrincipalForbidden,
    ApprovalRequestResult,
    DecideApprovalInput,
)
from enterprise_doc_core.context import PrincipalContext


class StubPrincipalResolver:
    def __init__(self, principal: PrincipalContext) -> None:
        self.principal = principal

    async def resolve(self, _: str) -> PrincipalContext:
        return self.principal


class StubApprovalService:
    def __init__(self, *, replayed: bool = False) -> None:
        self.replayed = replayed
        self.run_id = uuid4()
        self.job_id = uuid4()
        self.execution_id = uuid4()
        self.calls: list[dict[str, object]] = []
        self.get_calls: list[dict[str, object]] = []
        self.error: Exception | None = None

    async def get(self, **kwargs: object) -> ApprovalRequestResult:
        self.get_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return ApprovalRequestResult(
            approval_id=kwargs["approval_id"],  # type: ignore[arg-type]
            run_id=self.run_id,
            status="pending",
            operation="publish_artifact",
            target_resource_type="agent_artifact",
            target_resource_id=uuid4(),
            target_document_version_id=uuid4(),
            target_fingerprint="a" * 64,
            requested_at=datetime(2026, 7, 19, tzinfo=UTC),
            expires_at=datetime(2026, 7, 20, tzinfo=UTC),
            decided_at=None,
            can_decide=True,
        )

    async def decide(self, **kwargs: object) -> ApprovalDecisionResult:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        request = kwargs["request"]
        assert isinstance(request, DecideApprovalInput)
        return ApprovalDecisionResult(
            approval_id=kwargs["approval_id"],  # type: ignore[arg-type]
            run_id=self.run_id,
            status=request.decision,
            decision=str(request.decision),
            resume_job_id=self.job_id,
            resume_execution_id=self.execution_id,
            decision_fingerprint="f" * 64,
            replayed=self.replayed,
            decided_at=datetime(2026, 7, 19, tzinfo=UTC),
        )


def _principal() -> PrincipalContext:
    return PrincipalContext(tenant_id=str(uuid4()), actor_id=str(uuid4()), role="owner")


def _body() -> dict[str, object]:
    return {
        "decision": "approved",
        "operation": "publish_artifact",
        "targetResourceType": "agent_artifact",
        "targetResourceId": str(uuid4()),
        "targetDocumentVersionId": str(uuid4()),
        "targetFingerprint": "a" * 64,
        "comment": "Reviewed",
    }


def _app(principal: PrincipalContext, service: StubApprovalService):
    return create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(principal),
        upload_creation_service=object(),  # type: ignore[arg-type]
        upload_session_service=object(),  # type: ignore[arg-type]
        agent_run_service=object(),  # type: ignore[arg-type]
        approval_service=service,
    )


async def test_approval_decision_is_authenticated_exact_and_idempotent() -> None:
    principal = _principal()
    service = StubApprovalService()
    approval_id = uuid4()
    app = _app(principal, service)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing_auth = await client.post(
            f"/api/approvals/{approval_id}/decisions",
            headers={"Idempotency-Key": "decision-1"},
            json=_body(),
        )
        missing_key = await client.post(
            f"/api/approvals/{approval_id}/decisions",
            headers={"Authorization": "Bearer token"},
            json=_body(),
        )
        body = _body()
        decided = await client.post(
            f"/api/approvals/{approval_id}/decisions",
            headers={
                "Authorization": "Bearer token",
                "Idempotency-Key": "decision-1",
            },
            json=body,
        )

    assert missing_auth.status_code == 401
    assert missing_key.status_code == 400
    assert decided.status_code == 202
    assert decided.json()["resumeJobId"] == str(service.job_id)
    assert decided.json()["decisionFingerprint"] == "f" * 64
    call = service.calls[0]
    assert call["tenant_id"] == UUID(principal.tenant_id)
    assert call["actor_id"] == UUID(principal.actor_id)
    assert call["approval_id"] == approval_id
    assert call["idempotency_key"] == "decision-1"
    assert call["request"] == DecideApprovalInput(
        decision="approved",
        operation="publish_artifact",
        target_resource_type="agent_artifact",
        target_resource_id=UUID(str(body["targetResourceId"])),
        target_document_version_id=UUID(str(body["targetDocumentVersionId"])),
        target_fingerprint="a" * 64,
        comment="Reviewed",
    )


async def test_approval_detail_returns_the_exact_decision_target() -> None:
    principal = _principal()
    service = StubApprovalService()
    approval_id = uuid4()

    async with AsyncClient(
        transport=ASGITransport(app=_app(principal, service)),
        base_url="http://test",
    ) as client:
        response = await client.get(
            f"/api/approvals/{approval_id}",
            headers={"Authorization": "Bearer token"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "approvalId": str(approval_id),
        "runId": str(service.run_id),
        "status": "pending",
        "operation": "publish_artifact",
        "targetResourceType": "agent_artifact",
        "targetResourceId": response.json()["targetResourceId"],
        "targetDocumentVersionId": response.json()["targetDocumentVersionId"],
        "targetFingerprint": "a" * 64,
        "requestedAt": "2026-07-19T00:00:00Z",
        "expiresAt": "2026-07-20T00:00:00Z",
        "decidedAt": None,
        "canDecide": True,
    }
    assert service.get_calls == [
        {
            "tenant_id": UUID(principal.tenant_id),
            "actor_id": UUID(principal.actor_id),
            "approval_id": approval_id,
        }
    ]


async def test_approval_decision_replay_and_owner_denial_are_typed() -> None:
    principal = _principal()
    replay_service = StubApprovalService(replayed=True)
    denied_service = StubApprovalService()
    denied_service.error = ApprovalPrincipalForbidden()
    approval_id = uuid4()
    headers = {"Authorization": "Bearer token", "Idempotency-Key": "decision-1"}

    async with AsyncClient(
        transport=ASGITransport(app=_app(principal, replay_service)),
        base_url="http://test",
    ) as client:
        replay = await client.post(
            f"/api/approvals/{approval_id}/decisions",
            headers=headers,
            json=_body(),
        )
    async with AsyncClient(
        transport=ASGITransport(app=_app(principal, denied_service)),
        base_url="http://test",
    ) as client:
        denied = await client.post(
            f"/api/approvals/{approval_id}/decisions",
            headers=headers,
            json=_body(),
        )

    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "approval_principal_forbidden"
