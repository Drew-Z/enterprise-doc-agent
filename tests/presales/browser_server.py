"""Loopback-only browser fixture: real API/DB/retrieval, controlled HTTP model.

No source uploads, worker ingestion, external identity provider or paid model
requests occur here. All seeded tenant/user records are deleted on cleanup.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from uuid import uuid4

import httpx
import uvicorn
from fastapi import Header, HTTPException
from pydantic import SecretStr
from sqlalchemy import delete, func, select

from enterprise_doc_api.app import create_app
from enterprise_doc_api.auth.jwt import InvalidBearerToken
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.config import DatabaseSettings, ModelProvider, ModelSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.db import (
    create_database_engine,
    create_session_factory,
    selector_event_loop_factory,
)
from enterprise_doc_core.documents import Document, DocumentVersion, HashEmbeddingProvider
from enterprise_doc_core.documents.models import DocumentGrant
from enterprise_doc_core.documents.retrieval_service import HybridRetrievalService
from enterprise_doc_core.identity import Membership, Tenant, User
from enterprise_doc_core.presales.gateway import OpenAICompatiblePresalesGateway
from enterprise_doc_core.presales.models import PresalesPacket
from enterprise_doc_core.presales.schemas import GenerationInput, ModelDraft
from enterprise_doc_core.presales.service import PresalesService
from enterprise_doc_core.presales.settings import PresalesSettings
from tests.agent.test_agent_run_integration import SeededAgentContext, _seed_agent_context
from tests.presales.fixtures import add_chunk, add_document


async def main() -> None:
    engine = create_database_engine(DatabaseSettings())
    sessions = create_session_factory(engine)
    seeded: list[SeededAgentContext] = []
    cleaned = False

    async def cleanup() -> dict[str, int]:
        nonlocal cleaned
        if not seeded:
            cleaned = True
            return {"remainingTestTenants": 0}
        tenant_ids = [item.tenant_id for item in seeded]
        actor_ids = [item.actor_id for item in seeded]
        async with sessions.begin() as session:
            await session.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
            await session.execute(delete(User).where(User.id.in_(actor_ids)))
            remaining = await session.scalar(
                select(func.count()).select_from(Tenant).where(Tenant.id.in_(tenant_ids))
            )
        cleaned = True
        return {"remainingTestTenants": remaining or 0}

    try:
        for _ in range(2):
            seeded.append(await _seed_agent_context(sessions))
        context, other = seeded
        second, generation = await add_document(sessions, context)
        versions = [context.document_version_id, second]
        document_ids = []
        async with sessions.begin() as session:
            membership = await session.get(Membership, context.membership_id)
            assert membership is not None
            membership.role = "member"
            for version_id, filename in zip(
                versions, ["合成验收-保留策略.txt", "合成验收-备份条款.txt"], strict=True
            ):
                version = await session.get(DocumentVersion, version_id)
                assert version is not None
                version.original_filename = filename
                document = await session.get(Document, version.document_id)
                assert document is not None
                document.access_mode, document.created_by = "restricted", other.actor_id
                document.title = filename
                document_ids.append(document.id)
        await add_chunk(
            sessions,
            context,
            versions[0],
            context.generation_id,
            "Retention is 30 days. " + "Synthetic browser fixture. " * 100,
        )
        await add_chunk(sessions, context, versions[1], generation, "Retention is 90 days.")
        calls: Counter[str] = Counter()
        token = "presales-browser-" + uuid4().hex
        other_token = "presales-browser-" + uuid4().hex

        async def reset() -> None:
            calls.clear()
            async with sessions.begin() as session:
                await session.execute(
                    delete(PresalesPacket).where(PresalesPacket.tenant_id == context.tenant_id)
                )
                await session.execute(
                    delete(DocumentGrant).where(DocumentGrant.document_id.in_(document_ids))
                )
                for document_id in document_ids:
                    session.add(
                        DocumentGrant(
                            tenant_id=context.tenant_id,
                            document_id=document_id,
                            grantee_user_id=context.actor_id,
                        )
                    )

        await reset()

        class Resolver:
            async def resolve(self, candidate: str) -> PrincipalContext:
                if candidate == token:
                    return PrincipalContext(str(context.tenant_id), str(context.actor_id), "member")
                if candidate == other_token:
                    return other.principal
                raise InvalidBearerToken()

        async def model_response(request: httpx.Request) -> httpx.Response:
            envelope = json.loads(request.content)
            payload = GenerationInput.model_validate_json(envelope["messages"][1]["content"])
            text = payload.requirement.text
            calls[text] += 1
            if "timeout-once" in text and calls[text] == 1:
                raise httpx.ReadTimeout("controlled browser timeout", request=request)
            status = "supported"
            for name in (
                "conditional",
                "contradicted",
                "insufficient_evidence",
                "conflicting_evidence",
            ):
                if "[" + name + "]" in text:
                    status = name
            citations = [
                {
                    "chunkId": evidence["chunkId"],
                    "documentVersionId": evidence["documentVersionId"],
                    "excerpt": evidence["text"].split(".")[0] + ".",
                }
                for evidence in payload.evidence
            ]
            if status != "conflicting_evidence":
                citations = citations[:1]
            if status == "insufficient_evidence":
                citations = []
            draft = ModelDraft.model_validate(
                {
                    "status": status,
                    "answer": "受控浏览器验收输出。请核对合成资料中的保留期限。",
                    "conditions": ["需采用指定配置并确认合同范围。"]
                    if status == "conditional"
                    else [],
                    "missingInformation": ["请补充当前有效的证明材料。"]
                    if status == "insufficient_evidence"
                    else [],
                    "citations": citations,
                }
            )
            return httpx.Response(
                200,
                json={
                    "id": "controlled-" + uuid4().hex,
                    "model": "controlled-browser-fixture",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": draft.model_dump_json(by_alias=True),
                            },
                        }
                    ],
                },
            )

        model = ModelSettings(
            provider=ModelProvider.OPENAI_COMPATIBLE,
            base_url="https://presales-browser.invalid/v1",
            api_key=SecretStr("test-only-no-external-request"),
            model_name="controlled-browser-fixture",
        )
        service = PresalesService(
            session_factory=sessions,
            retriever=HybridRetrievalService(
                session_factory=sessions, embedding_provider=HashEmbeddingProvider()
            ),
            gateway=OpenAICompatiblePresalesGateway(
                model, transport=httpx.MockTransport(model_response)
            ),
            settings=PresalesSettings(generation_enabled=True),
        )
        app = create_app(
            settings=ApiSettings(_env_file=None),
            principal_resolver=Resolver(),
            presales_service=service,
        )
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=18765, access_log=False, log_level="warning")
        )

        def require_test_header(value: str | None) -> None:
            if value != "presales-browser":
                raise HTTPException(403)

        @app.get("/__presales_test__/context")
        async def test_context(x_presales_test: str | None = Header(default=None)) -> dict:
            require_test_header(x_presales_test)
            return {
                "token": token,
                "otherToken": other_token,
                "tenantId": str(context.tenant_id),
                "actorId": str(context.actor_id),
                "modelBoundary": "httpx.MockTransport; no external requests",
            }

        @app.post("/__presales_test__/reset")
        async def test_reset(x_presales_test: str | None = Header(default=None)) -> dict:
            require_test_header(x_presales_test)
            await reset()
            return {"reset": True}

        @app.get("/__presales_test__/stats")
        async def stats(x_presales_test: str | None = Header(default=None)) -> dict:
            require_test_header(x_presales_test)
            return {"mockProviderRequests": sum(calls.values()), "callsByRequirement": dict(calls)}

        @app.post("/__presales_test__/revoke")
        async def revoke(x_presales_test: str | None = Header(default=None)) -> dict:
            require_test_header(x_presales_test)
            async with sessions.begin() as session:
                await session.execute(
                    delete(DocumentGrant).where(DocumentGrant.document_id.in_(document_ids))
                )
            return {"revoked": True}

        @app.post("/__presales_test__/shutdown")
        async def shutdown(x_presales_test: str | None = Header(default=None)) -> dict:
            require_test_header(x_presales_test)
            result = await cleanup()
            server.should_exit = True
            return result

        await server.serve()
    finally:
        if not cleaned:
            await cleanup()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(), loop_factory=selector_event_loop_factory)
