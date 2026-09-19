from __future__ import annotations

import hashlib
from uuid import UUID, uuid4

from pydantic import SecretStr

from enterprise_doc_core.admission.contracts import VerifiedAdmissionIdentity
from enterprise_doc_core.browser_sessions.contracts import IssuedBrowserSession
from enterprise_doc_core.browser_sessions.service import BrowserSessionService
from enterprise_doc_core.identity.models import ExternalIdentityBinding, Membership, Tenant, User

from .conftest import BrowserDatabase

ISSUER = "https://browser-idp.example.test"
IDENTITY = VerifiedAdmissionIdentity(ISSUER, "subject-one", "owner@example.test", True)
RATE_KEY = hashlib.sha256(b"synthetic-client").hexdigest()


async def sign_in(
    service: BrowserSessionService, previous: SecretStr | None = None
) -> IssuedBrowserSession:
    start = await service.begin_login(rate_key=RATE_KEY, previous_credential=previous)
    claim = await service.claim_login(
        state=start.state, verifier=start.verifier, previous_credential=previous
    )
    return await service.complete_login(
        attempt_id=claim.attempt_id, identity=IDENTITY, previous_credential=previous
    )


async def seed_tenants(db: BrowserDatabase) -> tuple[UUID, UUID, UUID]:
    actor, first, second = uuid4(), uuid4(), uuid4()
    async with db.sessions.begin() as session:
        session.add(User(id=actor, email=IDENTITY.email))
        session.add_all(
            [
                Tenant(id=first, name="企业甲", slug="tenant-a", quota_bytes=1000000),
                Tenant(id=second, name="企业乙", slug="tenant-b", quota_bytes=1000000),
            ]
        )
        await session.flush()
        for tenant, role in ((first, "owner"), (second, "member")):
            session.add(Membership(tenant_id=tenant, user_id=actor, role=role))
            session.add(
                ExternalIdentityBinding(
                    tenant_id=tenant, user_id=actor, issuer=ISSUER, subject=IDENTITY.subject
                )
            )
    return actor, first, second
