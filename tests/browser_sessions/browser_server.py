"""Loopback-only OIDC HTTP IdP + real API/database browser acceptance harness.

Synthetic accounts, real RSA signatures, PKCE and durable browser sessions.
No production authentication resolver or route is replaced.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import os
import secrets
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from uuid import uuid4

import jwt
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import SecretStr
from sqlalchemy import func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema
from tests.browser_sessions.conftest import create_previous, migrate_browser

from enterprise_doc_api.app import create_app
from enterprise_doc_api.browser_auth.settings import BrowserAuthSettings
from enterprise_doc_api.config import ApiSettings, AuthSettings
from enterprise_doc_core.admission.contracts import (
    AdmissionGrantRequest,
    PlatformAdmissionOperator,
    prepare_admission_credential,
)
from enterprise_doc_core.admission.service import TenantAdmissionService
from enterprise_doc_core.browser_sessions.models import BrowserSession, BrowserSessionEvent
from enterprise_doc_core.db import create_session_factory, selector_event_loop_factory
from enterprise_doc_core.health import build_foundation_resources
from enterprise_doc_core.identity.models import ExternalIdentityBinding, Membership, Tenant, User
from enterprise_doc_core.uploads.models import UploadSession
from enterprise_doc_core.uploads.policy import build_object_key

WEB_ORIGIN = "http://127.0.0.1:5173"
IDP_ORIGIN = "http://localhost:18768"
CLIENT_ID = "synthetic-browser-acceptance"


class BrowserHarness:
    def __init__(self, output: Path) -> None:
        base = ApiSettings()
        endpoints = [
            base.database.url.get_secret_value(),
            base.redis.url.get_secret_value(),
            base.object_store.endpoint,
            base.object_store.presign_endpoint,
        ]
        if base.app_env not in {"local", "test"} or any(
            urlparse(value).hostname not in {"127.0.0.1", "localhost", "::1"} for value in endpoints
        ):
            raise RuntimeError("Browser acceptance requires loopback local/test infrastructure.")
        self.output = output
        self.run_id = uuid4().hex
        self.schema = "browser_e2e_" + self.run_id
        self.admin = create_async_engine(base.database.url.get_secret_value())
        database = base.database.model_copy(
            update={
                "url": SecretStr(
                    make_url(base.database.url.get_secret_value())
                    .update_query_dict({"options": f"-csearch_path={self.schema},public"})
                    .render_as_string(hide_password=False)
                )
            }
        )
        self.settings = ApiSettings(
            _env_file=None,
            app_env=base.app_env,
            database=database,
            redis=base.redis,
            object_store=base.object_store,
            upload=base.upload,
            auth=AuthSettings(signing_key=SecretStr(secrets.token_urlsafe(48))),
            browser_auth=BrowserAuthSettings(
                enabled=True,
                web_origin=WEB_ORIGIN,
                issuer=IDP_ORIGIN,
                authorization_endpoint=IDP_ORIGIN + "/authorize",
                token_endpoint=IDP_ORIGIN + "/token",
                jwks_url=IDP_ORIGIN + "/jwks",
                client_id=CLIENT_ID,
            ),
        )
        self.resources = build_foundation_resources(self.settings)
        self.sessions = create_session_factory(self.resources.database_engine)
        self.tenant_a, self.tenant_b, self.actor = uuid4(), uuid4(), uuid4()
        self.created_schema = False
        self.migrated = False
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.transactions: dict[str, dict] = {}
        self.codes: dict[str, dict] = {}
        self.admission_code: SecretStr | None = None
        self.protocol = {
            "authorizations": 0,
            "exchanges": 0,
            "pkceVerified": 0,
            "applicationCookiesAtIdp": 0,
        }

    def write(self, filename: str, value: object) -> None:
        (self.output / filename).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    async def start(self) -> None:
        async with self.admin.begin() as connection:
            await connection.execute(CreateSchema(self.schema))
            self.created_schema = True
        async with self.resources.database_engine.begin() as connection:
            assert await connection.scalar(text("SELECT current_schema()")) == self.schema
            await connection.run_sync(create_previous)
            await connection.run_sync(migrate_browser, "upgrade")
            self.migrated = True
        async with self.sessions.begin() as session:
            session.add(User(id=self.actor, email="browser-owner@example.test"))
            session.add_all(
                [
                    Tenant(
                        id=self.tenant_a,
                        name="澄明软件",
                        slug="browser-a-" + self.run_id,
                        quota_bytes=32 * 1024 * 1024,
                    ),
                    Tenant(
                        id=self.tenant_b,
                        name="远川服务",
                        slug="browser-b-" + self.run_id,
                        quota_bytes=32 * 1024 * 1024,
                    ),
                ]
            )
            await session.flush()
            for tenant_id, role in [(self.tenant_a, "owner"), (self.tenant_b, "member")]:
                session.add(Membership(tenant_id=tenant_id, user_id=self.actor, role=role))
                session.add(
                    ExternalIdentityBinding(
                        tenant_id=tenant_id,
                        user_id=self.actor,
                        issuer=IDP_ORIGIN,
                        subject="bound-owner",
                    )
                )
        prepared = prepare_admission_credential()
        self.admission_code = prepared.token
        service = TenantAdmissionService(
            session_factory=self.sessions, trusted_issuers=frozenset({IDP_ORIGIN})
        )
        await service.issue(
            operator=PlatformAdmissionOperator(
                "browser-acceptance", "Synthetic local browser acceptance"
            ),
            request=AdmissionGrantRequest(
                recipient_email="browser-new@example.test",
                issuer=IDP_ORIGIN,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                quota_bytes=32 * 1024 * 1024,
                seat_limit=2,
            ),
            credential=prepared,
        )
        self.write(
            "run-context.json",
            {
                "runId": self.run_id,
                "schema": self.schema,
                "tenantIds": [str(self.tenant_a), str(self.tenant_b)],
                "actorId": str(self.actor),
                "boundaries": {
                    "identity": "local signed HTTP IdP with PKCE",
                    "principal": "production database browser session service",
                    "upload": "real File/hash/HTTP/MinIO; no worker in this suite",
                    "model": "no model requests",
                    "customers": "synthetic accounts only",
                },
            },
        )

    async def snapshot(self) -> dict:
        async with self.sessions() as session:
            uploads = (await session.scalars(select(UploadSession))).all()
            events = (await session.scalars(select(BrowserSessionEvent))).all()
            tenants = int(await session.scalar(select(func.count()).select_from(Tenant)) or 0)
        return {
            "runId": self.run_id,
            "protocol": self.protocol,
            "tenantCount": tenants,
            "uploads": [
                {
                    "tenantId": str(item.tenant_id),
                    "filename": item.original_filename,
                    "status": item.status,
                    "sizeBytes": item.size_bytes,
                    "sha256": item.declared_sha256,
                }
                for item in uploads
            ],
            "sessionEvents": [
                {"action": item.action, "generation": item.generation} for item in events
            ],
        }

    async def cleanup(self) -> None:
        receipt: dict = {"runId": self.run_id, "schema": self.schema, "success": False}
        resources_closed = False
        try:
            if self.migrated:
                self.write("database-evidence.json", await self.snapshot())
                async with self.sessions() as session:
                    assert await session.scalar(text("SELECT current_schema()")) == self.schema
                    uploads = (await session.scalars(select(UploadSession))).all()
                    tenant_ids = set(await session.scalars(select(Tenant.id)))
                store, bucket = (
                    self.resources.multipart_object_store,
                    self.settings.object_store.documents_bucket,
                )
                aborted = 0
                remaining_objects = remaining_multipart = 0
                for upload in uploads:
                    expected_key = build_object_key(
                        session_id=upload.id, version_id=upload.pending_version_id
                    )
                    if upload.tenant_id not in tenant_ids or upload.object_key != expected_key:
                        raise RuntimeError("Object cleanup escaped the isolated upload session.")
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
                    listing = await asyncio.to_thread(
                        self.resources.object_store_client.list_objects_v2,
                        Bucket=bucket,
                        Prefix=upload.object_key,
                    )
                    remaining_objects += sum(
                        item["Key"] == upload.object_key for item in listing.get("Contents", [])
                    )
                    remaining_multipart += sum(
                        item.key == upload.object_key
                        for item in await store.list_incomplete_uploads(
                            bucket=bucket, prefix=upload.object_key
                        )
                    )
                receipt.update(
                    objectsDeleted=len(uploads),
                    multipartAborted=aborted,
                    remainingObjects=remaining_objects,
                    remainingMultipartUploads=remaining_multipart,
                )
                if remaining_objects or remaining_multipart:
                    raise RuntimeError("Test object resources remain.")
            await self.resources.close()
            resources_closed = True
            if self.created_schema:
                if self.schema != "browser_e2e_" + self.run_id:
                    raise RuntimeError("Unexpected test schema.")
                async with self.admin.begin() as connection:
                    await connection.execute(DropSchema(self.schema, cascade=True))
                async with self.admin.connect() as connection:
                    remains = await connection.scalar(
                        text("SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=:schema)"),
                        {"schema": self.schema},
                    )
                if remains:
                    raise RuntimeError("Test schema remains.")
                receipt["schemaRemoved"] = True
            receipt["success"] = True
        except Exception as error:
            receipt["errorType"] = type(error).__name__
            raise
        finally:
            if not resources_closed:
                await self.resources.close()
            await self.admin.dispose()
            self.write("cleanup.json", receipt)


def idp_app(harness: BrowserHarness, stop: asyncio.Event) -> FastAPI:
    app = FastAPI()

    def test_only(request: Request) -> None:
        if request.headers.get("X-Browser-Test") != "isolated-browser-session":
            raise HTTPException(403)

    @app.middleware("http")
    async def protect(request: Request, call_next):
        if "__Host-docagent" in request.headers.get("Cookie", ""):
            harness.protocol["applicationCookiesAtIdp"] += 1
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ready"}

    @app.get("/authorize")
    async def authorize(request: Request) -> HTMLResponse:
        params = dict(request.query_params)
        expected = {
            "client_id": CLIENT_ID,
            "response_type": "code",
            "redirect_uri": WEB_ORIGIN + "/auth/callback",
            "code_challenge_method": "S256",
        }
        if (
            any(params.get(key) != value for key, value in expected.items())
            or not params.get("nonce")
            or not params.get("state")
        ):
            raise HTTPException(400)
        transaction = secrets.token_urlsafe(24)
        harness.transactions[transaction] = params | {"started": time.monotonic()}
        harness.protocol["authorizations"] += 1
        return HTMLResponse(
            '<!doctype html><html lang="zh"><meta charset="utf-8">'
            "<title>本地签名测试身份服务</title><h1>本地签名测试身份服务</h1>"
            '<p>仅用于合成账号验收</p><form method="post" action="/authorize">'
            '<input type="hidden" name="transaction" value="'
            + html.escape(transaction)
            + '"><button name="account" value="bound">已绑定账号</button>'
            '<button name="account" value="new">新企业账号</button>'
            '<button name="account" value="same-email">同邮箱未绑定账号</button>'
            '<button name="account" value="cancel">取消登录</button></form></html>'
        )

    @app.post("/authorize")
    async def consent(request: Request) -> RedirectResponse:
        body = parse_qs((await request.body()).decode())
        entry = harness.transactions.pop(body.get("transaction", [""])[0], None)
        if entry is None or time.monotonic() - entry["started"] > 300:
            raise HTTPException(400)
        account = body.get("account", [""])[0]
        query = {"state": entry["state"], "iss": IDP_ORIGIN}
        if account == "cancel":
            query["error"] = "access_denied"
        elif account in {"bound", "new", "same-email"}:
            code = secrets.token_urlsafe(32)
            harness.codes[code] = entry | {"account": account}
            query["code"] = code
        else:
            raise HTTPException(400)
        return RedirectResponse(WEB_ORIGIN + "/auth/callback?" + urlencode(query), status_code=303)

    @app.post("/token")
    async def token(request: Request) -> dict:
        body = parse_qs((await request.body()).decode())
        entry = harness.codes.pop(body.get("code", [""])[0], None)
        verifier = body.get("code_verifier", [""])[0]
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        if (
            entry is None
            or time.monotonic() - entry["started"] > 300
            or entry["code_challenge"] != challenge
            or body.get("client_id") != [CLIENT_ID]
            or body.get("redirect_uri") != [WEB_ORIGIN + "/auth/callback"]
            or body.get("grant_type") != ["authorization_code"]
        ):
            raise HTTPException(400)
        harness.protocol["exchanges"] += 1
        harness.protocol["pkceVerified"] += 1
        subject, email = {
            "bound": ("bound-owner", "browser-owner@example.test"),
            "new": ("new-owner", "browser-new@example.test"),
            "same-email": ("different-subject", "browser-owner@example.test"),
        }[entry["account"]]
        now = int(datetime.now(UTC).timestamp())
        return {
            "id_token": jwt.encode(
                {
                    "iss": IDP_ORIGIN,
                    "aud": CLIENT_ID,
                    "sub": subject,
                    "email": email,
                    "email_verified": True,
                    "nonce": entry["nonce"],
                    "iat": now,
                    "exp": now + 300,
                },
                harness.key,
                algorithm="RS256",
                headers={"kid": "local-rsa"},
            ),
            "access_token": "synthetic-upstream-not-stored",
        }

    @app.get("/jwks")
    async def jwks() -> dict:
        public = jwt.algorithms.RSAAlgorithm.to_jwk(harness.key.public_key(), as_dict=True)
        return {"keys": [public | {"kid": "local-rsa", "alg": "RS256", "use": "sig"}]}

    @app.get("/test/context")
    async def context(request: Request) -> dict:
        test_only(request)
        assert harness.admission_code is not None
        return {
            "admissionCode": harness.admission_code.get_secret_value(),
            "tenantA": str(harness.tenant_a),
            "tenantB": str(harness.tenant_b),
        }

    @app.post("/test/expire")
    async def expire(request: Request) -> dict:
        test_only(request)
        async with harness.sessions.begin() as session:
            await session.execute(
                update(BrowserSession)
                .where(BrowserSession.revoked_at.is_(None))
                .values(
                    created_at=func.clock_timestamp() - timedelta(hours=2),
                    expires_at=func.clock_timestamp() - timedelta(hours=1),
                )
            )
        return {"expired": True}

    @app.post("/test/shutdown")
    async def shutdown(request: Request) -> dict:
        test_only(request)
        stop.set()
        return {"stopping": True}

    return app


async def main(output: Path) -> None:
    harness = BrowserHarness(output)
    servers: list[uvicorn.Server] = []
    try:
        await harness.start()
        stop = asyncio.Event()
        for app, port in [
            (create_app(settings=harness.settings), 18767),
            (idp_app(harness, stop), 18768),
        ]:
            servers.append(
                uvicorn.Server(
                    uvicorn.Config(
                        app,
                        host="127.0.0.1",
                        port=port,
                        access_log=False,
                        proxy_headers=False,
                        log_level="warning",
                    )
                )
            )

        async def run_server(server: uvicorn.Server) -> None:
            try:
                await server.serve()
            finally:
                stop.set()

        async def stop_servers() -> None:
            await stop.wait()
            for server in servers:
                server.should_exit = True

        await asyncio.gather(*(run_server(server) for server in servers), stop_servers())
    finally:
        for server in servers:
            server.should_exit = True
        await harness.cleanup()


if __name__ == "__main__":
    destination = Path(os.environ["BROWSER_SESSION_OUTPUT_DIR"]).resolve(strict=True)
    asyncio.run(main(destination), loop_factory=selector_event_loop_factory)
