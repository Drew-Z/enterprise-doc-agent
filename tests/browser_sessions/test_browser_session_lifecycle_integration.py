from __future__ import annotations

import hashlib
from uuid import uuid4

import pytest
from sqlalchemy import select

from enterprise_doc_core.admission.contracts import VerifiedAdmissionIdentity
from enterprise_doc_core.browser_sessions.errors import BrowserSessionInvalid
from enterprise_doc_core.browser_sessions.models import BrowserLoginAttempt, BrowserSession
from enterprise_doc_core.browser_sessions.service import BrowserSessionService
from enterprise_doc_core.identity.models import ExternalIdentityBinding, Membership, Tenant, User

from .conftest import BrowserDatabase

pytestmark = pytest.mark.integration
ISSUER = "https://browser-idp.example.test"
IDENTITY = VerifiedAdmissionIdentity(ISSUER, "subject-one", "owner@example.test", True)
RATE_KEY = hashlib.sha256(b"synthetic-client").hexdigest()


async def test_login_creates_only_digest_credentials_and_unselected_identity(
    browser_db: BrowserDatabase,
) -> None:
    service = BrowserSessionService(session_factory=browser_db.sessions, trusted_issuer=ISSUER)
    start = await service.begin_login(rate_key=RATE_KEY)
    claimed = await service.claim_login(state=start.state, verifier=start.verifier)
    issued = await service.complete_login(attempt_id=claimed.attempt_id, identity=IDENTITY)
    snapshot = await service.get_session(credential=issued.credential)
    assert snapshot.identity == IDENTITY
    assert snapshot.selected is None
    assert snapshot.generation == 1
    async with browser_db.sessions() as session:
        row = await session.scalar(select(BrowserSession))
        attempt = await session.scalar(select(BrowserLoginAttempt))
        assert row is not None and attempt is not None
        assert (
            row.credential_digest
            == hashlib.sha256(issued.credential.get_secret_value().encode()).hexdigest()
        )
        assert (
            attempt.state_digest
            == hashlib.sha256(start.state.get_secret_value().encode()).hexdigest()
        )
        assert attempt.completed_at is not None
        assert "token" not in BrowserSession.__table__.columns
        assert "verifier" not in BrowserLoginAttempt.__table__.columns


async def test_selection_uses_binding_rotates_and_logout_revokes(
    browser_db: BrowserDatabase,
) -> None:
    tenant_id, user_id, membership_id, binding_id = (uuid4() for _ in range(4))
    async with browser_db.sessions.begin() as session:
        session.add(Tenant(id=tenant_id, name="企业甲", slug="tenant-one", quota_bytes=1000000))
        session.add(User(id=user_id, email=IDENTITY.email))
        await session.flush()
        session.add(
            Membership(id=membership_id, tenant_id=tenant_id, user_id=user_id, role="owner")
        )
        session.add(
            ExternalIdentityBinding(
                id=binding_id,
                tenant_id=tenant_id,
                user_id=user_id,
                issuer=ISSUER,
                subject=IDENTITY.subject,
            )
        )
    service = BrowserSessionService(session_factory=browser_db.sessions, trusted_issuer=ISSUER)
    start = await service.begin_login(rate_key=RATE_KEY)
    claimed = await service.claim_login(state=start.state, verifier=start.verifier)
    issued = await service.complete_login(attempt_id=claimed.attempt_id, identity=IDENTITY)
    choices = await service.list_tenants(
        credential=issued.credential, context_version=issued.snapshot.context_version
    )
    assert [choice.name for choice in choices] == ["企业甲"]
    selected = await service.select_tenant(
        credential=issued.credential,
        context_version=issued.snapshot.context_version,
        tenant_id=tenant_id,
    )
    assert selected.snapshot.generation == 2
    assert selected.snapshot.expires_at == issued.snapshot.expires_at
    assert selected.snapshot.selected is not None
    assert selected.snapshot.selected.membership_id == membership_id
    principal = await service.authorize(
        credential=selected.credential, context_version=selected.snapshot.context_version
    )
    assert (principal.tenant_id, principal.actor_id, principal.role) == (
        str(tenant_id),
        str(user_id),
        "owner",
    )
    with pytest.raises(BrowserSessionInvalid):
        await service.get_session(credential=issued.credential)
    await service.logout(
        credential=selected.credential, context_version=selected.snapshot.context_version
    )
    with pytest.raises(BrowserSessionInvalid):
        await service.authorize(
            credential=selected.credential, context_version=selected.snapshot.context_version
        )
