from __future__ import annotations

from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.agents import (
    AgentArtifactNotFound,
    AgentArtifactPrincipalForbidden,
    AgentPrincipalForbidden,
    AgentRunNotFound,
    ApprovalNotFound,
    ApprovalPrincipalForbidden,
)
from enterprise_doc_core.context import PrincipalContext


class StubPrincipalResolver:
    def __init__(self, principal: PrincipalContext) -> None:
        self.principal = principal

    async def resolve(self, _: str) -> PrincipalContext:
        return self.principal


class DenyingAgentService:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def create(self, **_: object):
        raise self.error

    async def get_status(self, **_: object):
        raise self.error

    async def list_events(self, **_: object):
        raise self.error

    async def cancel(self, **_: object):
        raise self.error

    async def list_ready_document_versions(self, **_: object):
        raise self.error


class DenyingApprovalService:
    def __init__(self, detail_error: Exception, decision_error: Exception) -> None:
        self.detail_error = detail_error
        self.decision_error = decision_error

    async def get(self, **_: object):
        raise self.detail_error

    async def decide(self, **_: object):
        raise self.decision_error


class DenyingArtifactService:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def list_for_run(self, **_: object):
        raise self.error

    async def get_download(self, **_: object):
        raise self.error


def _principal(role: str = "member") -> PrincipalContext:
    return PrincipalContext(tenant_id=str(uuid4()), actor_id=str(uuid4()), role=role)


def _app(
    principal: PrincipalContext,
    *,
    agent_error: Exception,
    approval_detail_error: Exception,
    approval_decision_error: Exception,
    artifact_error: Exception,
):
    return create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(principal),
        upload_creation_service=object(),  # type: ignore[arg-type]
        upload_session_service=object(),  # type: ignore[arg-type]
        agent_run_service=DenyingAgentService(agent_error),  # type: ignore[arg-type]
        approval_service=DenyingApprovalService(  # type: ignore[arg-type]
            approval_detail_error,
            approval_decision_error,
        ),
        agent_artifact_service=DenyingArtifactService(artifact_error),  # type: ignore[arg-type]
    )


def _approval_body() -> dict[str, object]:
    return {
        "decision": "approved",
        "operation": "publish_artifact",
        "targetResourceType": "agent_artifact",
        "targetResourceId": str(uuid4()),
        "targetDocumentVersionId": str(uuid4()),
        "targetFingerprint": "a" * 64,
    }


async def test_foreign_tenant_resources_are_non_enumerating_across_agent_apis() -> None:
    run_id = uuid4()
    approval_id = uuid4()
    artifact_id = uuid4()
    app = _app(
        _principal(),
        agent_error=AgentRunNotFound(),
        approval_detail_error=ApprovalNotFound(),
        approval_decision_error=ApprovalNotFound(),
        artifact_error=AgentArtifactNotFound(),
    )
    headers = {"Authorization": "Bearer token"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        responses = [
            await client.get(f"/api/agent-runs/{run_id}", headers=headers),
            await client.get(f"/api/agent-runs/{run_id}/events", headers=headers),
            await client.get(f"/api/agent-runs/{run_id}/events/stream", headers=headers),
            await client.post(f"/api/agent-runs/{run_id}/cancel", headers=headers),
            await client.get(f"/api/approvals/{approval_id}", headers=headers),
            await client.get(f"/api/agent-runs/{run_id}/artifacts", headers=headers),
            await client.get(f"/api/agent-artifacts/{artifact_id}/download", headers=headers),
        ]

    assert [response.status_code for response in responses] == [404] * len(responses)
    assert [response.json()["error"]["code"] for response in responses] == [
        "agent_run_not_found",
        "agent_run_not_found",
        "agent_run_not_found",
        "agent_run_not_found",
        "approval_not_found",
        "agent_artifact_not_found",
        "agent_artifact_not_found",
    ]
    encoded = " ".join(response.text for response in responses)
    assert str(run_id) not in encoded
    assert str(approval_id) not in encoded
    assert str(artifact_id) not in encoded
    assert "object_key" not in encoded.lower()


async def test_member_cannot_create_or_decide_publish_and_gets_no_artifact_location() -> None:
    principal = _principal("member")
    run_id = uuid4()
    approval_id = uuid4()
    artifact_id = uuid4()
    version_id = uuid4()
    app = _app(
        principal,
        agent_error=AgentPrincipalForbidden(),
        approval_detail_error=ApprovalPrincipalForbidden(),
        approval_decision_error=ApprovalPrincipalForbidden(),
        artifact_error=AgentArtifactPrincipalForbidden(),
    )
    headers = {"Authorization": "Bearer token"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/api/agent-runs",
            headers={**headers, "Idempotency-Key": "member-create-denied"},
            json={
                "documentVersionId": str(version_id),
                "taskType": "question_answer",
                "inputText": "Publish this now.",
                "publishRequested": True,
            },
        )
        decided = await client.post(
            f"/api/approvals/{approval_id}/decisions",
            headers={**headers, "Idempotency-Key": "member-decision-denied"},
            json=_approval_body(),
        )
        artifacts = await client.get(f"/api/agent-runs/{run_id}/artifacts", headers=headers)
        download = await client.get(
            f"/api/agent-artifacts/{artifact_id}/download",
            headers=headers,
        )

    assert created.status_code == 403
    assert created.json()["error"]["code"] == "agent_principal_forbidden"
    assert decided.status_code == 403
    assert decided.json()["error"]["code"] == "approval_principal_forbidden"
    assert artifacts.status_code == 403
    assert download.status_code == 403
    assert "url" not in download.json()
    assert "object" not in download.text.lower()
    assert UUID(principal.tenant_id) != version_id
