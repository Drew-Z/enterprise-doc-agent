from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select
from tests.agent.test_agent_run_integration import _seed_agent_context

from enterprise_doc_core.audit import AuditEvent
from enterprise_doc_core.config import DatabaseSettings
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.identity import ExternalIdentityBinding, Membership, User
from enterprise_doc_core.identity.scim_service import (
    ScimProvisioningConflict,
    ScimProvisioningService,
)

pytestmark = pytest.mark.integration


async def test_scim_sync_is_idempotent_tenant_scoped_and_audited() -> None:
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    context = await _seed_agent_context(session_factory)
    other_context = await _seed_agent_context(session_factory)
    service = ScimProvisioningService(session_factory=session_factory)
    issuer = " https://idp.example.test/scim "
    subject = f"scim-user-{uuid4().hex}"
    email = f"scim-{uuid4().hex}@example.test"
    updated_email = f"scim-updated-{uuid4().hex}@example.test"

    try:
        provisioned = await service.sync_user(
            tenant_id=context.tenant_id,
            issuer=issuer,
            subject=subject,
            email=email.upper(),
            role="member",
            is_active=True,
            request_id="scim-provision",
            correlation_id="scim-round-trip",
        )
        assert provisioned is not None
        assert provisioned.email == email
        assert provisioned.role == "member"
        assert provisioned.is_active is True
        read_back = await service.get_user(
            tenant_id=context.tenant_id,
            issuer=issuer,
            subject=subject,
        )
        assert read_back is not None
        assert read_back.membership_id == provisioned.membership_id
        assert read_back.email == email
        assert read_back.last_modified is not None
        listed = await service.list_users(
            tenant_id=context.tenant_id,
            issuer=issuer,
            start_index=1,
            count=1,
        )
        assert listed is not None
        assert listed.total_results == 1
        assert listed.items_per_page == 1
        assert listed.resources[0].subject == subject
        filtered = await service.list_users(
            tenant_id=context.tenant_id,
            issuer=issuer,
            user_name=email,
        )
        assert filtered is not None
        assert [resource.subject for resource in filtered.resources] == [subject]

        replayed = await service.sync_user(
            tenant_id=context.tenant_id,
            issuer=issuer,
            subject=subject,
            email=email,
            role="member",
            is_active=True,
        )
        assert replayed is not None
        assert replayed.membership_id == provisioned.membership_id
        assert replayed.binding_id == provisioned.binding_id

        updated = await service.sync_user(
            tenant_id=context.tenant_id,
            issuer=issuer,
            subject=subject,
            email=updated_email,
            role="member",
            is_active=True,
            request_id="scim-update",
            correlation_id="scim-round-trip",
        )
        assert updated is not None
        assert updated.email == updated_email

        deprovisioned = await service.sync_user(
            tenant_id=context.tenant_id,
            issuer=issuer,
            subject=subject,
            email=None,
            role=None,
            is_active=False,
            request_id="scim-deprovision",
            correlation_id="scim-round-trip",
        )
        assert deprovisioned is not None
        assert deprovisioned.is_active is False
        inactive_read = await service.get_user(
            tenant_id=context.tenant_id,
            issuer=issuer,
            subject=subject,
        )
        assert inactive_read is not None
        assert inactive_read.is_active is False

        replayed_deprovision = await service.sync_user(
            tenant_id=context.tenant_id,
            issuer=issuer,
            subject=subject,
            email=None,
            role=None,
            is_active=False,
        )
        assert replayed_deprovision is not None
        assert replayed_deprovision.is_active is False

        # A deprovision request in another tenant cannot affect this binding.
        assert (
            await service.sync_user(
                tenant_id=other_context.tenant_id,
                issuer=issuer,
                subject=subject,
                email=None,
                role=None,
                is_active=False,
            )
            is None
        )
        assert (
            await service.get_user(
                tenant_id=other_context.tenant_id,
                issuer=issuer,
                subject=subject,
            )
            is None
        )

        async with session_factory() as session:
            membership = await session.scalar(
                select(Membership).where(Membership.id == provisioned.membership_id)
            )
            binding = await session.scalar(
                select(ExternalIdentityBinding).where(
                    ExternalIdentityBinding.id == provisioned.binding_id
                )
            )
            user = await session.scalar(select(User).where(User.id == provisioned.user_id))
            actions = (
                await session.scalars(
                    select(AuditEvent.action)
                    .where(
                        AuditEvent.tenant_id == context.tenant_id,
                        AuditEvent.resource_id == provisioned.membership_id,
                    )
                    .order_by(AuditEvent.occurred_at.asc(), AuditEvent.id.asc())
                )
            ).all()
        assert membership is not None and membership.is_active is False
        assert binding is not None and binding.is_active is False
        assert user is not None and user.email == updated_email
        assert actions == [
            "scim.user.provisioned",
            "scim.user.updated",
            "scim.user.deprovisioned",
        ]
    finally:
        await engine.dispose()


async def test_scim_deprovision_cannot_remove_the_last_owner() -> None:
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    context = await _seed_agent_context(session_factory)
    service = ScimProvisioningService(session_factory=session_factory)
    subject = f"scim-owner-{uuid4().hex}"

    try:
        async with session_factory() as session:
            owner = await session.scalar(select(User).where(User.id == context.actor_id))
        assert owner is not None

        provisioned = await service.sync_user(
            tenant_id=context.tenant_id,
            issuer="https://idp.example.test/scim",
            subject=subject,
            email=owner.email,
            role="owner",
            is_active=True,
        )
        assert provisioned is not None

        with pytest.raises(ScimProvisioningConflict):
            await service.sync_user(
                tenant_id=context.tenant_id,
                issuer="https://idp.example.test/scim",
                subject=subject,
                email=None,
                role=None,
                is_active=False,
            )

        async with session_factory() as session:
            membership = await session.scalar(
                select(Membership).where(Membership.id == provisioned.membership_id)
            )
            binding = await session.scalar(
                select(ExternalIdentityBinding).where(
                    ExternalIdentityBinding.id == provisioned.binding_id
                )
            )
            actions = (
                await session.scalars(
                    select(AuditEvent.action).where(
                        AuditEvent.resource_id == provisioned.membership_id,
                    )
                )
            ).all()
        assert membership is not None and membership.is_active is True
        assert binding is not None and binding.is_active is True
        assert actions == ["scim.user.provisioned"]
    finally:
        await engine.dispose()
