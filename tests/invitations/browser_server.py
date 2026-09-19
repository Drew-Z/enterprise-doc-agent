"""Isolated invitation acceptance: signed loopback IdP and unmodified production API."""

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
from sqlalchemy import Connection, MetaData, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from enterprise_doc_api.app import create_app
from enterprise_doc_api.browser_auth.settings import BrowserAuthSettings
from enterprise_doc_api.config import ApiSettings, AuthSettings
from enterprise_doc_core.admission.contracts import (
    AdmissionGrantRequest,
    PlatformAdmissionOperator,
    VerifiedAdmissionIdentity,
    prepare_admission_credential,
)
from enterprise_doc_core.admission.service import TenantAdmissionService
from enterprise_doc_core.browser_sessions.models import BrowserSessionEvent
from enterprise_doc_core.db import create_session_factory, selector_event_loop_factory
from enterprise_doc_core.db.metadata import metadata
from enterprise_doc_core.identity.models import ExternalIdentityBinding, Membership, Tenant, User
from enterprise_doc_core.invitations.contracts import InvitationSettings
from enterprise_doc_core.invitations.models import MembershipInvitation, MembershipInvitationEvent
from tests.invitations.conftest import INVITATION_TABLES, migrate_invitations

WEB_ORIGIN = "http://127.0.0.1:5173"
IDP_ORIGIN = "http://localhost:18770"
CLIENT_ID = "synthetic-invitation-acceptance"
ACCOUNTS = {
    "owner": ("invitation-owner", "invitation-owner@example.test", "企业管理员"),
    "desktop": ("desktop-colleague", "desktop-colleague@example.test", "桌面受邀同事"),
    "mobile": ("mobile-colleague", "mobile-colleague@example.test", "手机受邀同事"),
    "outsider": ("other-colleague", "other-colleague@example.test", "其他邮箱账号"),
    "same-email": ("unbound-owner", "invitation-owner@example.test", "同邮箱不同身份"),
}


def create_previous(connection: Connection) -> None:
    previous = MetaData(naming_convention=metadata.naming_convention)
    for table in metadata.tables.values():
        if table.name not in INVITATION_TABLES:
            table.to_metadata(previous)
    previous.create_all(connection, checkfirst=False)
    names = set(
        connection.scalars(
            text("SELECT tablename FROM pg_tables WHERE schemaname=current_schema()")
        )
    )
    assert names == set(previous.tables)


class InvitationHarness:
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
            raise RuntimeError("Invitation acceptance requires loopback local/test infrastructure.")
        self.output = output
        self.run_id = uuid4().hex
        self.schema = "invitation_e2e_" + self.run_id
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
            invitations=InvitationSettings(enabled=True),
        )
        self.engine = create_async_engine(database.url.get_secret_value())
        self.sessions = create_session_factory(self.engine)
        self.created_schema = False
        self.migrated = False
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.transactions: dict[str, dict] = {}
        self.codes: dict[str, dict] = {}
        self.protocol = {
            "authorizations": 0,
            "exchanges": 0,
            "pkceVerified": 0,
            "applicationCookiesAtIdp": 0,
        }

    def write(self, filename: str, value: object) -> None:
        with (self.output / filename).open("x", encoding="utf-8") as target:
            target.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")

    async def start(self) -> None:
        async with self.admin.begin() as connection:
            await connection.execute(CreateSchema(self.schema))
            self.created_schema = True
        async with self.engine.begin() as connection:
            assert await connection.scalar(text("SELECT current_schema()")) == self.schema
            await connection.run_sync(create_previous)
            await connection.run_sync(migrate_invitations, "upgrade")
            self.migrated = True
        admission = TenantAdmissionService(
            session_factory=self.sessions, trusted_issuers=frozenset({IDP_ORIGIN})
        )
        for name in ("澄明软件", "远川服务"):
            prepared = prepare_admission_credential()
            await admission.issue(
                operator=PlatformAdmissionOperator(
                    "invitation-browser-test", "Synthetic owner setup only"
                ),
                request=AdmissionGrantRequest(
                    recipient_email=ACCOUNTS["owner"][1],
                    issuer=IDP_ORIGIN,
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                    quota_bytes=32 * 1024 * 1024,
                    seat_limit=2,
                ),
                credential=prepared,
            )
            await admission.accept(
                token=prepared.token,
                tenant_name=name,
                identity=VerifiedAdmissionIdentity(
                    issuer=IDP_ORIGIN,
                    subject=ACCOUNTS["owner"][0],
                    email=ACCOUNTS["owner"][1],
                    email_verified=True,
                ),
            )
        async with self.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(MembershipInvitation)) == 0
        self.write(
            "run-context.json",
            {
                "runId": self.run_id,
                "schema": self.schema,
                "pid": os.getpid(),
                "webOrigin": WEB_ORIGIN,
                "apiPort": 18769,
                "idpOrigin": IDP_ORIGIN,
                "invitationCountAtStart": 0,
                "boundary": (
                    "Synthetic owner admission setup; invitations created and accepted only "
                    "through the browser UI. Local signed HTTP IdP, real API/PostgreSQL, "
                    "no email, workers, uploads, model requests or customers."
                ),
            },
        )

    async def snapshot(self) -> dict:
        async with self.sessions() as session:
            assert await session.scalar(text("SELECT current_schema()")) == self.schema
            tenants = (await session.scalars(select(Tenant))).all()
            active = {
                tenant.name: int(
                    await session.scalar(
                        select(func.count())
                        .select_from(Membership)
                        .where(Membership.tenant_id == tenant.id, Membership.is_active.is_(True))
                    )
                    or 0
                )
                for tenant in tenants
            }
            events = (await session.scalars(select(MembershipInvitationEvent))).all()
            invitations = (await session.scalars(select(MembershipInvitation))).all()
            session_events = (await session.scalars(select(BrowserSessionEvent))).all()
            users = int(await session.scalar(select(func.count()).select_from(User)) or 0)
            outsiders = int(
                await session.scalar(
                    select(func.count())
                    .select_from(User)
                    .where(User.email == ACCOUNTS["outsider"][1])
                )
                or 0
            )
            bindings = int(
                await session.scalar(select(func.count()).select_from(ExternalIdentityBinding)) or 0
            )
        return {
            "runId": self.run_id,
            "protocol": self.protocol,
            "activeMembers": active,
            "userCount": users,
            "outsiderUsers": outsiders,
            "bindingCount": bindings,
            "invitations": [
                {"state": value.state, "generation": value.generation} for value in invitations
            ],
            "invitationEvents": [
                {
                    "action": value.action,
                    "generation": value.generation,
                    "requestLinked": value.request_id is not None,
                }
                for value in events
            ],
            "sessionEvents": [
                {"action": value.action, "generation": value.generation} for value in session_events
            ],
        }

    async def cleanup(self) -> None:
        result = {
            "runId": self.run_id,
            "schema": self.schema,
            "success": False,
            "schemaRemoved": False,
            "objectWrites": 0,
            "workerStarts": 0,
            "externalModelRequests": 0,
            "emailsSent": 0,
        }
        try:
            if self.migrated:
                self.write("database-evidence.json", await self.snapshot())
            await self.engine.dispose()
            if self.created_schema:
                if self.schema != "invitation_e2e_" + self.run_id:
                    raise RuntimeError("Unexpected invitation test schema.")
                async with self.admin.begin() as connection:
                    await connection.execute(DropSchema(self.schema, cascade=True))
                async with self.admin.connect() as connection:
                    assert not await connection.scalar(
                        text("SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=:schema)"),
                        {"schema": self.schema},
                    )
                result["schemaRemoved"] = True
            result["success"] = True
        except Exception as error:
            result["errorType"] = type(error).__name__
            raise
        finally:
            await self.engine.dispose()
            await self.admin.dispose()
            self.write("cleanup.json", result)


def idp_app(harness: InvitationHarness, stop: asyncio.Event) -> FastAPI:
    app = FastAPI()

    def test_only(request: Request) -> None:
        if request.headers.get("X-Invitation-Run") != harness.run_id:
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
        buttons = "".join(
            f'<button name="account" value="{key}">{html.escape(value[2])}</button>'
            for key, value in ACCOUNTS.items()
        )
        return HTMLResponse(
            '<!doctype html><html lang="zh"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            "<title>本地签名邀请测试</title><h1>本地签名测试身份服务</h1><p>仅用于合成账号验收</p>"
            '<form method="post" action="/authorize">'
            '<input type="hidden" name="transaction" value="'
            + html.escape(transaction)
            + '">'
            + buttons
            + "</form></html>"
        )

    @app.post("/authorize")
    async def authorize_account(request: Request) -> RedirectResponse:
        body = parse_qs((await request.body()).decode())
        entry = harness.transactions.pop(body.get("transaction", [""])[0], None)
        account = body.get("account", [""])[0]
        if entry is None or account not in ACCOUNTS or time.monotonic() - entry["started"] > 300:
            raise HTTPException(400)
        code = secrets.token_urlsafe(32)
        harness.codes[code] = entry | {"account": account}
        return RedirectResponse(
            WEB_ORIGIN
            + "/auth/callback?"
            + urlencode({"state": entry["state"], "iss": IDP_ORIGIN, "code": code}),
            status_code=303,
        )

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
        subject, email, _ = ACCOUNTS[entry["account"]]
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
                headers={"kid": "invitation-rsa"},
            ),
            "access_token": "synthetic-upstream-not-stored",
        }

    @app.get("/jwks")
    async def jwks() -> dict:
        public = jwt.algorithms.RSAAlgorithm.to_jwk(harness.key.public_key(), as_dict=True)
        return {"keys": [public | {"kid": "invitation-rsa", "alg": "RS256", "use": "sig"}]}

    @app.get("/test/state")
    async def state(request: Request) -> dict:
        test_only(request)
        return await harness.snapshot()

    @app.post("/test/shutdown")
    async def shutdown(request: Request) -> dict:
        test_only(request)
        stop.set()
        return {"stopping": True, "runId": harness.run_id}

    return app


async def main(output: Path) -> None:
    if any(
        (output / name).exists()
        for name in ("run-context.json", "cleanup.json", "database-evidence.json")
    ):
        raise RuntimeError("Use a fresh dedicated invitation acceptance directory.")
    harness = InvitationHarness(output)
    servers: list[uvicorn.Server] = []
    tasks: list[asyncio.Task] = []
    try:
        await harness.start()
        stop = asyncio.Event()
        for app, port in [
            (create_app(settings=harness.settings), 18769),
            (idp_app(harness, stop), 18770),
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
        tasks = [asyncio.create_task(server.serve()) for server in servers]
        stopped = asyncio.create_task(stop.wait())
        try:
            await asyncio.wait([*tasks, stopped], return_when=asyncio.FIRST_COMPLETED)
        finally:
            for server in servers:
                server.should_exit = True
            await asyncio.gather(*tasks)
            stopped.cancel()
            await asyncio.gather(stopped, return_exceptions=True)
    finally:
        for server in servers:
            server.should_exit = True
        await harness.cleanup()


if __name__ == "__main__":
    destination = Path(os.environ["INVITATION_OUTPUT_DIR"]).resolve(strict=True)
    asyncio.run(main(destination), loop_factory=selector_event_loop_factory)
