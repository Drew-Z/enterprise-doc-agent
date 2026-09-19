from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from enterprise_doc_core.admission.models import TenantAdmissionGrant, TenantInitialEntitlement
from enterprise_doc_core.identity.models import ExternalIdentityBinding, Membership, Tenant, User
from enterprise_doc_core.invitations.contracts import InvitationSettings
from enterprise_doc_core.invitations.service import MembershipInvitationService

from .conftest import InvitationDatabase

ISSUER = "https://invitation-idp.example.test"


def invitation_service(db: InvitationDatabase) -> MembershipInvitationService:
    return MembershipInvitationService(
        session_factory=db.sessions,
        settings=InvitationSettings(enabled=True),
        trusted_issuer=ISSUER,
    )


@dataclass(frozen=True)
class TenantOwner:
    tenant_id: UUID
    user_id: UUID
    membership_id: UUID
    email: str
    subject: str


async def tenant_owner(db: InvitationDatabase, seats: int | None = 2) -> TenantOwner:
    tenant, user, membership, grant = uuid4(), uuid4(), uuid4(), uuid4()
    email, subject = f"owner-{user.hex}@example.test", f"owner-{user.hex}"
    now = datetime.now(UTC)
    async with db.sessions.begin() as session:
        session.add(
            Tenant(id=tenant, name="邀请测试企业", slug=f"t-{tenant.hex}", quota_bytes=1000000)
        )
        session.add(User(id=user, email=email))
        if seats is not None:
            session.add(
                TenantAdmissionGrant(
                    id=grant,
                    token_digest=hashlib.sha256(grant.bytes).hexdigest(),
                    recipient_email=email,
                    issuer=ISSUER,
                    quota_bytes=1000000,
                    seat_limit=seats,
                    issued_at=now,
                    expires_at=now + timedelta(days=1),
                )
            )
        await session.flush()
        session.add(Membership(id=membership, tenant_id=tenant, user_id=user, role="owner"))
        session.add(
            ExternalIdentityBinding(
                tenant_id=tenant,
                user_id=user,
                issuer=ISSUER,
                subject=subject,
            )
        )
        if seats is not None:
            session.add(
                TenantInitialEntitlement(
                    tenant_id=tenant,
                    grant_id=grant,
                    quota_bytes=1000000,
                    seat_limit=seats,
                    created_at=now,
                )
            )
    return TenantOwner(tenant, user, membership, email, subject)
