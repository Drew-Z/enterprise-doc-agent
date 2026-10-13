from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from enterprise_doc_api.app import create_app
from enterprise_doc_api.auth.jwt import InvalidBearerToken
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.analysis.schemas import AnalysisSpec, ComparisonObject, default_fields
from enterprise_doc_core.analysis.service import AnalysisWorkspaceService
from enterprise_doc_core.config import UploadSettings
from enterprise_doc_core.context import PrincipalContext
from tests.analysis.test_source_workspace_integration import seed_source

pytestmark = pytest.mark.integration


async def test_analysis_http_requires_identity_etag_and_allows_read_after_admission_closes(
    browser_db,
) -> None:
    tenant, actor, version, _ = await seed_source(browser_db.sessions)

    class Resolver:
        async def resolve(self, token: str) -> PrincipalContext:
            if token != "analysis-test-token":
                raise InvalidBearerToken()
            return PrincipalContext(str(tenant), str(actor), "member")

    app = create_app(
        settings=ApiSettings(upload=UploadSettings(structured_documents_enabled=True)),
        checkers=(),
        principal_resolver=Resolver(),
        analysis_service=AnalysisWorkspaceService(session_factory=browser_db.sessions),
    )
    headers = {"Authorization": "Bearer analysis-test-token"}
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app), base_url="http://test") as client,
    ):
        assert (await client.get("/api/analysis/projects")).status_code == 401
        capabilities = await client.get("/api/analysis/capabilities", headers=headers)
        assert capabilities.json()["structuredUploadsEnabled"] is True
        spec = AnalysisSpec(
            objects=(ComparisonObject(id="a", name="A", source_version_ids=(version,)),),
            fields=default_fields(),
        )
        created = await client.post(
            "/api/analysis/projects",
            headers=headers,
            json={"title": "方案评审", "spec": spec.model_dump(mode="json", by_alias=True)},
        )
        assert created.status_code == 201, created.text
        route = "/api/analysis/projects/" + created.json()["id"]
        assert created.headers["etag"] == '"1"'
        assert (await client.post(route + "/snapshots", headers=headers)).status_code == 428
        stale = await client.post(route + "/snapshots", headers=headers | {"If-Match": '"2"'})
        assert stale.status_code == 409
        frozen = await client.post(route + "/snapshots", headers=headers | {"If-Match": '"1"'})
        assert frozen.status_code == 200, frozen.text
        blocks_route = f"/api/analysis/snapshots/{frozen.json()['id']}/sources/{version}/blocks"
        result = await client.get(blocks_route, headers=headers)
        assert result.json()["blocks"][0]["locator"]["line"] == 1
        assert "blockId" in result.json()["blocks"][0]
        app.state.analysis_admission_enabled = False
        assert (
            await client.post(route + "/snapshots", headers=headers | {"If-Match": '"1"'})
        ).status_code == 409
        assert (await client.get(blocks_route, headers=headers)).status_code == 200
