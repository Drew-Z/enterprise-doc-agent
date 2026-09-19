from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from enterprise_doc_core.admission.contracts import (
    VerifiedAdmissionIdentity,
    prepare_admission_credential,
)
from enterprise_doc_core.admission.errors import AdmissionDenied
from enterprise_doc_core.admission.models import (
    TenantAdmissionEvent,
    TenantAdmissionGrant,
    TenantInitialEntitlement,
)
from enterprise_doc_core.audit.models import AuditEvent
from enterprise_doc_core.identity.models import ExternalIdentityBinding, Membership, Tenant, User
from tests.admission.conftest import AdmissionDatabase
from tests.admission.test_admission_lifecycle_integration import (
    ISSUER,
    OPERATOR,
    grant_request,
    service_for,
)

pytestmark = pytest.mark.integration
IDENTITY = VerifiedAdmissionIdentity(ISSUER, "verified-subject", "owner@example.test", True)


async def assert_no_account(database: AdmissionDatabase) -> None:
    async with database.sessions() as session:
        for model in (
            Tenant,
            User,
            Membership,
            ExternalIdentityBinding,
            TenantInitialEntitlement,
            AuditEvent,
        ):
            assert await session.scalar(select(func.count()).select_from(model)) == 0
        assert await session.scalar(select(func.count()).select_from(TenantAdmissionEvent)) == 1


async def test_accept_creates_complete_account_and_both_audits(
    admission_db: AdmissionDatabase,
) -> None:
    service = service_for(admission_db)
    credential = prepare_admission_credential()
    await service.issue(operator=OPERATOR, request=grant_request(), credential=credential)
    receipt = await service.accept(
        token=credential.token, identity=IDENTITY, tenant_name=" 示例企业 "
    )
    assert receipt.grant_id == credential.grant_id and not receipt.replayed
    async with admission_db.sessions() as session:
        tenant = await session.get(Tenant, receipt.tenant_id)
        user = await session.get(User, receipt.user_id)
        membership = await session.get(Membership, receipt.membership_id)
        binding = await session.get(ExternalIdentityBinding, receipt.binding_id)
        entitlement = await session.get(TenantInitialEntitlement, receipt.entitlement_id)
        assert tenant is not None and tenant.name == "示例企业"
        assert tenant.quota_bytes == 4096 and tenant.is_active
        assert tenant.used_storage_bytes == tenant.reserved_storage_bytes == 0
        assert tenant.slug == receipt.tenant_slug
        assert user is not None and user.email == IDENTITY.email and user.is_active
        assert membership is not None and membership.role == "owner" and membership.is_active
        assert binding is not None and binding.is_active
        assert binding.issuer == IDENTITY.issuer and binding.subject == IDENTITY.subject
        assert (
            entitlement is not None
            and entitlement.seat_limit == 3
            and entitlement.quota_bytes == 4096
        )
        assert binding.tenant_id == membership.tenant_id == entitlement.tenant_id == tenant.id
        assert binding.user_id == membership.user_id == user.id
        assert entitlement.grant_id == credential.grant_id
        assert await session.scalar(select(func.count()).select_from(TenantAdmissionEvent)) == 2
        audit = await session.scalar(select(AuditEvent))
        assert audit is not None and audit.action == "tenant.admission.accepted"
        assert audit.tenant_id == tenant.id and audit.actor_id == user.id
        assert IDENTITY.email not in str(audit.event_metadata)
        assert credential.token.get_secret_value() not in str(audit.event_metadata)
    assert (await service.show(operator=OPERATOR, grant_id=credential.grant_id)).state == "accepted"


async def test_audit_failure_rolls_back_account_and_consumption(
    admission_db: AdmissionDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = service_for(admission_db)
    credential = prepare_admission_credential()
    await service.issue(operator=OPERATOR, request=grant_request(), credential=credential)

    async def fail_audit(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr("enterprise_doc_core.admission.service.append_audit_event", fail_audit)
    with pytest.raises(RuntimeError, match="synthetic audit failure"):
        await service.accept(
            token=credential.token, identity=IDENTITY, tenant_name="Atomic company"
        )
    await assert_no_account(admission_db)
    assert (await service.show(operator=OPERATOR, grant_id=credential.grant_id)).state == "pending"


@pytest.mark.parametrize(
    "case", ["unverified", "wrong_email", "wrong_issuer", "revoked", "expired"]
)
async def test_unauthorized_acceptance_has_no_side_effects(
    admission_db: AdmissionDatabase, case: str
) -> None:
    service = service_for(admission_db)
    credential = prepare_admission_credential()
    await service.issue(operator=OPERATOR, request=grant_request(), credential=credential)
    identity = IDENTITY
    if case == "unverified":
        identity = replace(identity, email_verified=False)
    elif case == "wrong_email":
        identity = replace(identity, email="other@example.test")
    elif case == "wrong_issuer":
        identity = replace(identity, issuer="https://untrusted.example.test")
    elif case == "revoked":
        await service.revoke(operator=OPERATOR, grant_id=credential.grant_id)
    elif case == "expired":
        async with admission_db.sessions.begin() as session:
            grant = await session.get(TenantAdmissionGrant, credential.grant_id)
            assert grant is not None
            grant.issued_at = datetime.now(UTC) - timedelta(days=2)
            grant.expires_at = datetime.now(UTC) - timedelta(days=1)
    with pytest.raises(AdmissionDenied):
        await service.accept(token=credential.token, identity=identity, tenant_name="Unauthorized")
    async with admission_db.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Tenant)) == 0
        assert await session.scalar(select(func.count()).select_from(User)) == 0
        assert await session.scalar(select(func.count()).select_from(AuditEvent)) == 0
