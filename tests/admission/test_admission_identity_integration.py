from __future__ import annotations

from dataclasses import asdict, replace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from enterprise_doc_core.admission.contracts import AdmissionReceipt, prepare_admission_credential
from enterprise_doc_core.admission.errors import (
    AdmissionAccountConflict,
    AdmissionConflict,
    AdmissionDenied,
)
from enterprise_doc_core.admission.models import (
    TenantAdmissionEvent,
    TenantAdmissionGrant,
    TenantInitialEntitlement,
)
from enterprise_doc_core.identity.models import ExternalIdentityBinding, Membership, Tenant, User
from tests.admission.conftest import AdmissionDatabase
from tests.admission.test_admission_acceptance_integration import IDENTITY
from tests.admission.test_admission_lifecycle_integration import (
    OPERATOR,
    grant_request,
    service_for,
)

pytestmark = pytest.mark.integration


async def first_account(database: AdmissionDatabase) -> AdmissionReceipt:
    service = service_for(database)
    credential = prepare_admission_credential()
    await service.issue(operator=OPERATOR, request=grant_request(), credential=credential)
    return await service.accept(
        token=credential.token, identity=IDENTITY, tenant_name="Existing company"
    )


async def test_same_request_replays_original_receipt_after_company_rename(
    admission_db: AdmissionDatabase,
) -> None:
    service = service_for(admission_db)
    credential = prepare_admission_credential()
    await service.issue(operator=OPERATOR, request=grant_request(), credential=credential)
    receipt = await service.accept(
        token=credential.token, identity=IDENTITY, tenant_name=" Original "
    )
    async with admission_db.sessions.begin() as session:
        tenant = await session.get(Tenant, receipt.tenant_id)
        assert tenant is not None
        tenant.name = "Renamed company"
    replay = await service.accept(token=credential.token, identity=IDENTITY, tenant_name="Original")
    assert asdict(replace(replay, replayed=False)) == asdict(receipt)
    assert replay.replayed
    for identity, name in (
        (IDENTITY, "Renamed company"),
        (replace(IDENTITY, subject="other"), "Original"),
    ):
        with pytest.raises(AdmissionDenied):
            await service.accept(token=credential.token, identity=identity, tenant_name=name)
    with pytest.raises(AdmissionConflict):
        await service.revoke(operator=OPERATOR, grant_id=credential.grant_id)
    async with admission_db.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Tenant)) == 1
        assert await session.scalar(select(func.count()).select_from(TenantAdmissionEvent)) == 2


async def test_existing_explicit_identity_reuses_user_in_new_company(
    admission_db: AdmissionDatabase,
) -> None:
    existing = await first_account(admission_db)
    service = service_for(admission_db)
    credential = prepare_admission_credential()
    await service.issue(operator=OPERATOR, request=grant_request(), credential=credential)
    created = await service.accept(
        token=credential.token, identity=IDENTITY, tenant_name="Second company"
    )
    assert created.tenant_id != existing.tenant_id and created.user_id == existing.user_id
    assert (
        created.binding_id != existing.binding_id
        and created.membership_id != existing.membership_id
    )
    async with admission_db.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 1
        assert await session.scalar(select(func.count()).select_from(Tenant)) == 2
        assert await session.scalar(select(func.count()).select_from(Membership)) == 2


@pytest.mark.parametrize(
    "case",
    [
        "user",
        "tenant",
        "membership",
        "binding",
        "email_changed",
        "ambiguous_binding",
        "ambiguous_email",
    ],
)
async def test_conflicted_or_inactive_account_is_not_merged_or_restored(
    admission_db: AdmissionDatabase, case: str
) -> None:
    previous = await first_account(admission_db)
    async with admission_db.sessions.begin() as session:
        if case in {"user", "tenant", "membership", "binding"}:
            model, entity_id = {
                "user": (User, previous.user_id),
                "tenant": (Tenant, previous.tenant_id),
                "membership": (Membership, previous.membership_id),
                "binding": (ExternalIdentityBinding, previous.binding_id),
            }[case]
            entity = await session.get(model, entity_id)
            assert entity is not None
            entity.is_active = False
        elif case == "email_changed":
            user = await session.get(User, previous.user_id)
            assert user is not None
            user.email = "changed@example.test"
        elif case == "ambiguous_email":
            session.add(User(id=uuid4(), email=IDENTITY.email.upper(), is_active=True))
        else:
            user = User(id=uuid4(), email="different@example.test", is_active=True)
            tenant = Tenant(
                id=uuid4(), slug=f"ambiguous-{uuid4().hex}", name="Other", quota_bytes=1024
            )
            session.add_all((user, tenant))
            await session.flush()
            session.add_all(
                (
                    Membership(tenant_id=tenant.id, user_id=user.id, role="owner", is_active=True),
                    ExternalIdentityBinding(
                        tenant_id=tenant.id,
                        user_id=user.id,
                        issuer=IDENTITY.issuer,
                        subject=IDENTITY.subject,
                        is_active=True,
                    ),
                )
            )
    service = service_for(admission_db)
    credential = prepare_admission_credential()
    await service.issue(operator=OPERATOR, request=grant_request(), credential=credential)
    async with admission_db.sessions() as session:
        tenant_count = await session.scalar(select(func.count()).select_from(Tenant))
        user_count = await session.scalar(select(func.count()).select_from(User))
    with pytest.raises(AdmissionAccountConflict):
        await service.accept(
            token=credential.token, identity=IDENTITY, tenant_name="Must not create"
        )
    async with admission_db.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Tenant)) == tenant_count
        assert await session.scalar(select(func.count()).select_from(User)) == user_count
        if case in {"user", "tenant", "membership", "binding"}:
            entity = await session.get(model, entity_id)
            assert entity is not None and not entity.is_active
    assert (await service.show(operator=OPERATOR, grant_id=credential.grant_id)).state == "pending"


async def test_same_email_without_binding_cannot_claim_existing_account(
    admission_db: AdmissionDatabase,
) -> None:
    async with admission_db.sessions.begin() as session:
        session.add(User(id=uuid4(), email=IDENTITY.email, is_active=True))
    service = service_for(admission_db)
    credential = prepare_admission_credential()
    await service.issue(operator=OPERATOR, request=grant_request(), credential=credential)
    with pytest.raises(AdmissionAccountConflict):
        await service.accept(
            token=credential.token, identity=IDENTITY, tenant_name="No automatic link"
        )
    async with admission_db.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Tenant)) == 0
        assert await session.scalar(select(func.count()).select_from(User)) == 1
        assert await session.scalar(select(func.count()).select_from(ExternalIdentityBinding)) == 0


@pytest.mark.parametrize(
    "case",
    [
        "tenant",
        "user",
        "membership",
        "binding",
        "demoted",
        "deleted_tenant",
        "deleted_user",
        "deleted_entitlement",
    ],
)
async def test_consumed_grant_never_restores_inactive_or_deleted_entities(
    admission_db: AdmissionDatabase, case: str
) -> None:
    service = service_for(admission_db)
    credential = prepare_admission_credential()
    await service.issue(operator=OPERATOR, request=grant_request(), credential=credential)
    receipt = await service.accept(
        token=credential.token, identity=IDENTITY, tenant_name="Recorded company"
    )
    async with admission_db.sessions.begin() as session:
        model, entity_id = {
            "tenant": (Tenant, receipt.tenant_id),
            "user": (User, receipt.user_id),
            "membership": (Membership, receipt.membership_id),
            "binding": (ExternalIdentityBinding, receipt.binding_id),
            "demoted": (Membership, receipt.membership_id),
            "deleted_tenant": (Tenant, receipt.tenant_id),
            "deleted_user": (User, receipt.user_id),
            "deleted_entitlement": (TenantInitialEntitlement, receipt.entitlement_id),
        }[case]
        entity = await session.get(model, entity_id)
        assert entity is not None
        if case.startswith("deleted_"):
            await session.delete(entity)
        elif case == "demoted":
            entity.role = "member"
        else:
            entity.is_active = False
    with pytest.raises(AdmissionDenied):
        await service.accept(
            token=credential.token, identity=IDENTITY, tenant_name="Recorded company"
        )
    async with admission_db.sessions() as session:
        grant = await session.get(TenantAdmissionGrant, credential.grant_id)
        assert (
            grant is not None
            and grant.state == "accepted"
            and grant.accepted_tenant_id == receipt.tenant_id
        )
        assert await session.scalar(select(func.count()).select_from(TenantAdmissionEvent)) == 2
        entity = await session.get(model, entity_id)
        if case.startswith("deleted_"):
            assert entity is None
        elif case == "demoted":
            assert entity is not None and entity.role == "member"
        else:
            assert entity is not None and not entity.is_active
