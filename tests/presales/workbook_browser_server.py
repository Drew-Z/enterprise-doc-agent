"""Loopback browser fixture with an owned PostgreSQL schema and controlled model."""

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import uvicorn
from fastapi import Header, HTTPException, Response
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from enterprise_doc_api.app import create_app
from enterprise_doc_api.auth.jwt import InvalidBearerToken
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.db import selector_event_loop_factory
from enterprise_doc_core.documents import DocumentInventoryService
from tests.browser_sessions.conftest import browser_db
from tests.presales.test_presales_workflow_integration import workspace
from tests.presales.test_workbook import questionnaire


async def main():
    databases = browser_db.__wrapped__()
    db = await anext(databases)
    evidence = Path(os.environ["WORKBOOK_E2E_OUTPUT_DIR"])
    await asyncio.to_thread(evidence.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(
        (evidence / "owned-schema.json").write_text,
        json.dumps({"schema": db.schema}),
        encoding="utf-8",
    )
    states = workspace.__wrapped__(db)
    service, sessions, context, _, gateway, _ = await anext(states)
    token = "workbook-test-" + uuid4().hex
    settings = ApiSettings(_env_file=None)
    url = make_url(settings.database.url.get_secret_value()).update_query_dict(
        {"options": f"-csearch_path={db.schema},public"}
    )
    settings.database.url = SecretStr(url.render_as_string(hide_password=False))

    class Resolver:
        async def resolve(self, candidate):
            if candidate != token:
                raise InvalidBearerToken()
            return context.principal

    app = create_app(
        settings=settings,
        principal_resolver=Resolver(),
        presales_service=service,
        document_inventory_service=DocumentInventoryService(session_factory=sessions),
    )

    def require_header(value):
        if value != "workbook-browser":
            raise HTTPException(403)

    @app.get("/__workbook_test__/context")
    async def test_context(x_workbook_test: str | None = Header(default=None)):
        require_header(x_workbook_test)
        return {"token": token, "versionId": str(context.document_version_id)}

    @app.get("/__workbook_test__/fixture")
    async def fixture(count: int = 2, x_workbook_test: str | None = Header(default=None)):
        require_header(x_workbook_test)
        if count not in {2, 13}:
            raise HTTPException(400)
        return Response(questionnaire(count), media_type="application/octet-stream")

    @app.get("/__workbook_test__/stats")
    async def stats(x_workbook_test: str | None = Header(default=None)):
        require_header(x_workbook_test)
        return {"modelCalls": len(gateway.calls)}

    @app.post("/__workbook_test__/cleanup")
    async def cleanup(x_workbook_test: str | None = Header(default=None)):
        require_header(x_workbook_test)
        await states.aclose()
        await databases.aclose()
        return {"ownedSchemaRemoved": db.schema}

    try:
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=18765, access_log=False, log_level="warning")
        )
        await server.serve()
    finally:
        await states.aclose()
        await databases.aclose()


if __name__ == "__main__":
    with asyncio.Runner(loop_factory=selector_event_loop_factory) as runner:
        runner.run(main())
