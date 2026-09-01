from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.agents import (
    AgentArtifactDownloadResult,
    AgentArtifactIntegrityError,
    AgentArtifactNotFound,
    AgentArtifactPreviewResult,
    AgentArtifactResult,
    BehaviorVersions,
)
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.documents import ResolvedCitation


class StubPrincipalResolver:
    def __init__(self, principal: PrincipalContext) -> None:
        self.principal = principal

    async def resolve(self, _: str) -> PrincipalContext:
        return self.principal


class StubArtifactService:
    def __init__(self) -> None:
        self.run_id = uuid4()
        self.artifact_id = uuid4()
        self.document_version_id = uuid4()
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.error: Exception | None = None

    async def list_for_run(self, **kwargs: object) -> tuple[AgentArtifactResult, ...]:
        self.calls.append(("list", kwargs))
        if self.error is not None:
            raise self.error
        return (
            AgentArtifactResult(
                artifact_id=self.artifact_id,
                run_id=self.run_id,
                document_version_id=self.document_version_id,
                kind="answer",
                status="published",
                content_type="text/markdown",
                content_sha256="a" * 64,
                size_bytes=128,
                created_at=datetime(2026, 7, 19, tzinfo=UTC),
                verified_at=datetime(2026, 7, 19, tzinfo=UTC),
                published_at=datetime(2026, 7, 19, tzinfo=UTC),
            ),
        )

    async def get_download(self, **kwargs: object) -> AgentArtifactDownloadResult:
        self.calls.append(("download", kwargs))
        if self.error is not None:
            raise self.error
        return AgentArtifactDownloadResult(
            artifact_id=self.artifact_id,
            status="published",
            content_type="text/markdown",
            content_sha256="a" * 64,
            size_bytes=128,
            url="https://object.test/signed-answer",
            expires_in_seconds=300,
        )

    async def get_preview(self, **kwargs: object) -> AgentArtifactPreviewResult:
        self.calls.append(("preview", kwargs))
        if self.error is not None:
            raise self.error
        return AgentArtifactPreviewResult(
            artifact_id=self.artifact_id,
            run_id=self.run_id,
            document_version_id=self.document_version_id,
            status="published",
            content_sha256="a" * 64,
            schema_version=1,
            task_type="question_answer",
            answer_text="Payment is due within 30 days.",
            structured_fields=None,
            risk_hint="low",
            citations=(
                ResolvedCitation(
                    chunk_id=uuid4(),
                    document_version_id=self.document_version_id,
                    source_filename="contract.pdf",
                    page_number=3,
                    heading="Payment terms",
                    start_offset=120,
                    end_offset=168,
                    excerpt="Invoices are payable within thirty calendar days.",
                ),
            ),
            behavior_versions=BehaviorVersions(
                graph_version="graph-v1",
                prompt_version="prompt-v1",
                tool_schema_version="tool-v1",
            ),
        )


def _principal() -> PrincipalContext:
    return PrincipalContext(tenant_id=str(uuid4()), actor_id=str(uuid4()), role="owner")


def _app(principal: PrincipalContext, service: StubArtifactService):
    return create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=StubPrincipalResolver(principal),
        upload_creation_service=object(),  # type: ignore[arg-type]
        upload_session_service=object(),  # type: ignore[arg-type]
        agent_run_service=object(),  # type: ignore[arg-type]
        approval_service=object(),  # type: ignore[arg-type]
        agent_artifact_service=service,
    )


async def test_artifact_list_and_download_are_authenticated_and_tenant_scoped() -> None:
    principal = _principal()
    service = StubArtifactService()
    headers = {"Authorization": "Bearer token"}

    async with AsyncClient(
        transport=ASGITransport(app=_app(principal, service)),
        base_url="http://test",
    ) as client:
        missing_auth = await client.get(f"/api/agent-runs/{service.run_id}/artifacts")
        artifacts = await client.get(
            f"/api/agent-runs/{service.run_id}/artifacts",
            headers=headers,
        )
        preview = await client.get(
            f"/api/agent-artifacts/{service.artifact_id}",
            headers=headers,
        )
        download = await client.get(
            f"/api/agent-artifacts/{service.artifact_id}/download",
            headers=headers,
        )

    assert missing_auth.status_code == 401
    assert artifacts.status_code == 200
    assert artifacts.json()[0]["artifactId"] == str(service.artifact_id)
    assert "objectKey" not in artifacts.json()[0]
    assert preview.status_code == 200
    assert preview.json()["answerText"] == "Payment is due within 30 days."
    assert preview.json()["citations"][0]["sourceFilename"] == "contract.pdf"
    assert preview.json()["behaviorVersions"]["graphVersion"] == "graph-v1"
    assert "objectKey" not in preview.text
    assert "url" not in preview.json()
    assert download.status_code == 200
    assert download.json()["url"] == "https://object.test/signed-answer"
    expected_scope = {
        "tenant_id": UUID(principal.tenant_id),
        "actor_id": UUID(principal.actor_id),
    }
    assert service.calls == [
        ("list", {**expected_scope, "run_id": service.run_id}),
        ("preview", {**expected_scope, "artifact_id": service.artifact_id}),
        ("download", {**expected_scope, "artifact_id": service.artifact_id}),
    ]


async def test_artifact_errors_are_typed_without_enumerating_private_locations() -> None:
    principal = _principal()
    not_found = StubArtifactService()
    not_found.error = AgentArtifactNotFound()
    integrity = StubArtifactService()
    integrity.error = AgentArtifactIntegrityError()
    headers = {"Authorization": "Bearer token"}

    async with AsyncClient(
        transport=ASGITransport(app=_app(principal, not_found)),
        base_url="http://test",
    ) as client:
        missing = await client.get(
            f"/api/agent-artifacts/{not_found.artifact_id}/download",
            headers=headers,
        )
    async with AsyncClient(
        transport=ASGITransport(app=_app(principal, integrity)),
        base_url="http://test",
    ) as client:
        mismatched = await client.get(
            f"/api/agent-artifacts/{integrity.artifact_id}/download",
            headers=headers,
        )

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "agent_artifact_not_found"
    assert mismatched.status_code == 409
    assert mismatched.json()["error"]["code"] == "agent_artifact_integrity_error"
    assert "object" not in mismatched.text.lower()
