from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from enterprise_doc_api.app import create_app
from enterprise_doc_api.auth.jwt import JwtTokenCodec
from enterprise_doc_api.browser_auth.http import SESSION_COOKIE
from enterprise_doc_api.browser_auth.settings import BrowserAuthSettings
from enterprise_doc_api.config import ApiSettings, AuthSettings
from enterprise_doc_core.admission.contracts import VerifiedAdmissionIdentity
from enterprise_doc_core.browser_sessions.service import BrowserSessionService
from enterprise_doc_core.invitations.contracts import CreateInvitation, InvitationSettings

from .conftest import InvitationDatabase
from .support import ISSUER, invitation_service, tenant_owner

pytestmark = pytest.mark.integration
ORIGIN = "https://invitation-app.example.test"


def build_invitation_app(
    invitation_db: InvitationDatabase,
    auth: AuthSettings | None = None,
) -> FastAPI:
    url = invitation_db.engine.url.update_query_dict(
        {"options": f"-csearch_path={invitation_db.schema},public"}
    )
    settings = ApiSettings(
        _env_file=None,
        database={"url": url.render_as_string(hide_password=False)},
        invitations=InvitationSettings(enabled=True),
        auth=auth or AuthSettings(),
        browser_auth=BrowserAuthSettings(
            enabled=True,
            web_origin=ORIGIN,
            issuer=ISSUER,
            authorization_endpoint=ISSUER + "/authorize",
            token_endpoint=ISSUER + "/token",
            jwks_url=ISSUER + "/jwks",
            client_id="invitation-http",
        ),
    )
    return create_app(settings=settings, checkers=[])


@pytest.fixture
async def invitation_app(invitation_db: InvitationDatabase) -> AsyncIterator[FastAPI]:
    app = build_invitation_app(invitation_db)
    async with app.router.lifespan_context(app):
        yield app


async def sign_in(
    app: FastAPI,
    client: httpx.AsyncClient,
    identity: VerifiedAdmissionIdentity,
) -> dict:
    # HTTP authorization uses the actual durable session service. OIDC signature
    # verification has its own tests and the separate signed-HTTP browser harness.
    service: BrowserSessionService = app.state.browser_session_service
    start = await service.begin_login(
        rate_key=hashlib.sha256(identity.subject.encode()).hexdigest()
    )
    claim = await service.claim_login(state=start.state, verifier=start.verifier)
    issued = await service.complete_login(attempt_id=claim.attempt_id, identity=identity)
    client.cookies.set(
        SESSION_COOKIE,
        issued.credential.get_secret_value(),
        domain="invitation-app.example.test",
        path="/",
    )
    response = await client.get("/auth/session")
    assert response.status_code == 200
    return response.json()


def auth_headers(snapshot: dict) -> dict[str, str]:
    return {
        "Origin": ORIGIN,
        "X-Session-Context": snapshot["contextVersion"],
        "X-CSRF-Token": snapshot["csrfToken"],
    }


async def test_http_owner_invites_and_unselected_recipient_joins(
    invitation_app: FastAPI,
    invitation_db: InvitationDatabase,
) -> None:
    owner = await tenant_owner(invitation_db)
    transport = httpx.ASGITransport(app=invitation_app)
    async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as admin:
        initial = await sign_in(
            invitation_app,
            admin,
            VerifiedAdmissionIdentity(
                ISSUER,
                owner.subject,
                owner.email,
                True,
            ),
        )
        selected = await admin.post(
            "/auth/tenant",
            headers=auth_headers(initial),
            json={"tenantId": str(owner.tenant_id)},
        )
        assert selected.status_code == 200
        headers = auth_headers(selected.json())
        request = {"email": "http-recipient@example.test", "operationId": str(uuid4())}
        created = await admin.post("/api/invitations", headers=headers, json=request)
        assert created.status_code == 200
        token = created.json()["token"]
        assert isinstance(token, str) and len(token) == 48
        replay = await admin.post("/api/invitations", headers=headers, json=request)
        assert replay.status_code == 200 and replay.json()["replayed"]
        assert replay.json()["token"] is None
        listed = await admin.get("/api/invitations", headers=headers)
        assert listed.status_code == 200
        assert listed.json()["seats"] == {"active": 1, "limit": 2, "remaining": 1}
        assert "token" not in listed.json()["items"][0]
        async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as recipient:
            session = await sign_in(
                invitation_app,
                recipient,
                VerifiedAdmissionIdentity(
                    ISSUER,
                    "http-recipient",
                    request["email"],
                    True,
                ),
            )
            assert session["currentTenant"] is None
            preview = await recipient.post(
                "/auth/invitations/inspect",
                headers=auth_headers(session),
                json={"token": token},
            )
            assert preview.status_code == 200
            assert preview.json()["tenantName"] == "邀请测试企业"
            accepted = await recipient.post(
                "/auth/invitations/accept",
                headers=auth_headers(session),
                json={"token": token},
            )
            assert accepted.status_code == 200 and not accepted.json()["replayed"]
            assert accepted.json()["tenantId"] == str(owner.tenant_id)
            assert (await recipient.get("/auth/session")).json()["currentTenant"] is None
            choices = await recipient.get("/auth/tenants", headers=auth_headers(session))
            assert [row["tenantId"] for row in choices.json()] == [str(owner.tenant_id)]
            current = await recipient.post(
                "/auth/tenant",
                headers=auth_headers(session),
                json={"tenantId": str(owner.tenant_id)},
            )
            assert current.status_code == 200
            denied = await recipient.get("/api/invitations", headers=auth_headers(current.json()))
            assert denied.status_code == 403
        listed = await admin.get("/api/invitations", headers=headers)
        assert listed.json()["seats"]["active"] == 2
        assert listed.json()["items"][0]["state"] == "accepted"


@pytest.mark.parametrize("writer", ["members", "scim"])
async def test_old_http_writers_report_seat_conflict_safely(
    invitation_db: InvitationDatabase,
    writer: str,
) -> None:
    owner = await tenant_owner(invitation_db, seats=1)
    scim_token = "synthetic-scim-token-" + "x" * 32
    auth = AuthSettings(
        scim_enabled=True,
        scim_issuer=ISSUER,
        scim_tenant_tokens={str(owner.tenant_id): SecretStr(scim_token)},
    )
    app = build_invitation_app(invitation_db, auth=auth)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url=ORIGIN,
        ) as client:
            if writer == "members":
                bearer = JwtTokenCodec(auth).issue_local_token(
                    tenant_id=owner.tenant_id,
                    actor_id=owner.user_id,
                )
                response = await client.post(
                    "/api/members",
                    headers={"Authorization": f"Bearer {bearer}"},
                    json={"email": "http-seat@example.test", "role": "member"},
                )
            else:
                response = await client.put(
                    f"/scim/v2/tenants/{owner.tenant_id}/Users/http-seat",
                    headers={"Authorization": f"Bearer {scim_token}"},
                    json={
                        "userName": "http-seat@example.test",
                        "externalId": "http-seat",
                        "active": True,
                        "groups": [{"value": "member"}],
                    },
                )
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "membership_seat_limit_reached"


@pytest.mark.parametrize("endpoint", ["inspect", "accept"])
async def test_acceptance_http_requires_csrf_context_and_server_identity(
    invitation_app: FastAPI,
    invitation_db: InvitationDatabase,
    endpoint: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    owner = await tenant_owner(invitation_db)
    identity = VerifiedAdmissionIdentity(ISSUER, "safe-http", "safe-http@example.test", True)
    issued = await invitation_service(invitation_db).create(
        tenant_id=owner.tenant_id,
        actor_id=owner.user_id,
        request=CreateInvitation(email=identity.email, operation_id=uuid4()),
    )
    assert issued.token is not None
    token = issued.token.get_secret_value()
    path = f"/auth/invitations/{endpoint}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=invitation_app),
        base_url=ORIGIN,
    ) as client:
        session = await sign_in(invitation_app, client, identity)
        good = auth_headers(session)
        for changed, expected in (
            ({"Origin": "https://foreign.example.test"}, 403),
            ({"X-CSRF-Token": "x" * 64}, 403),
            ({"X-Session-Context": "0" * 32 + ".1"}, 409),
            ({"Authorization": "Bearer wrong-mode"}, 400),
        ):
            response = await client.post(path, headers=good | changed, json={"token": token})
            assert response.status_code == expected
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["referrer-policy"] == "no-referrer"
        duplicate = await client.post(
            path,
            headers=[*good.items(), ("X-Session-Context", session["contextVersion"])],
            json={"token": token},
        )
        assert duplicate.status_code == 409
        forged = await client.post(
            path,
            headers=good,
            json={
                "token": token,
                "issuer": ISSUER,
                "subject": identity.subject,
                "emailVerified": True,
                "role": "owner",
            },
        )
        assert forged.status_code == 422
        assert forged.json()["error"]["code"] == "request_validation_failed"
        cookie = client.cookies.get(SESSION_COOKIE)
        assert bool(token not in caplog.text)
        assert cookie is not None and bool(cookie not in caplog.text)
        logged_out = await client.post("/auth/logout", headers=good, json={})
        assert logged_out.status_code == 200
        denied = await client.post(path, headers=good, json={"token": token})
        assert denied.status_code == 401


async def test_bearer_owner_management_is_strict_and_tenant_scoped(
    invitation_app: FastAPI,
    invitation_db: InvitationDatabase,
) -> None:
    owner = await tenant_owner(invitation_db)
    other = await tenant_owner(invitation_db)
    token = JwtTokenCodec(invitation_app.state.auth_settings).issue_local_token(
        tenant_id=owner.tenant_id,
        actor_id=owner.user_id,
    )
    foreign = await invitation_service(invitation_db).create(
        tenant_id=other.tenant_id,
        actor_id=other.user_id,
        request=CreateInvitation(email="foreign@example.test", operation_id=uuid4()),
    )
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=invitation_app),
        base_url=ORIGIN,
    ) as client:
        bad = await client.post(
            "/api/invitations",
            headers=headers,
            json={
                "email": "new@example.test",
                "operationId": str(uuid4()),
                "role": "owner",
            },
        )
        assert bad.status_code == 422
        denied = await client.post(
            f"/api/invitations/{foreign.invitation.invitation_id}/revoke",
            headers=headers,
            json={"expectedGeneration": 1, "operationId": str(uuid4())},
        )
        assert denied.status_code == 404
        created = await client.post(
            "/api/invitations",
            headers=headers,
            json={
                "email": "own@example.test",
                "operationId": str(uuid4()),
            },
        )
        assert created.status_code == 200
        invitation_id = created.json()["invitation"]["invitationId"]
        regenerated = await client.post(
            f"/api/invitations/{invitation_id}/regenerate",
            headers=headers,
            json={"expectedGeneration": 1, "operationId": str(uuid4())},
        )
        assert regenerated.status_code == 200
        assert regenerated.json()["invitation"]["generation"] == 2
        stale = await client.post(
            f"/api/invitations/{invitation_id}/revoke",
            headers=headers,
            json={"expectedGeneration": 1, "operationId": str(uuid4())},
        )
        assert stale.status_code == 409
        revoked = await client.post(
            f"/api/invitations/{invitation_id}/revoke",
            headers=headers,
            json={"expectedGeneration": 2, "operationId": str(uuid4())},
        )
        assert revoked.status_code == 200 and revoked.json()["token"] is None
        assert revoked.json()["invitation"]["state"] == "revoked"
