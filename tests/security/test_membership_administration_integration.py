from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select
from tests.agent.test_agent_run_integration import _seed_agent_context

from enterprise_doc_api.auth import DatabaseExternalMembershipResolver
from enterprise_doc_core.audit import AuditEvent
from enterprise_doc_core.config import DatabaseSettings
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.identity.membership_service import (
    MembershipAdministrationService,
    MembershipLastOwnerRequired,
    MembershipSelfMutationForbidden,
)
from enterprise_doc_core.identity.service import ExternalIdentityBindingService

pytestmark = pytest.mark.integration


async def test_membership_lifecycle_is_tenant_scoped_safe_and_audited() -> None:
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    context = await _seed_agent_context(session_factory)
    other_context = await _seed_agent_context(session_factory)
    service = MembershipAdministrationService(session_factory=session_factory)
    binding_service = ExternalIdentityBindingService(session_factory=session_factory)
    resolver = DatabaseExternalMembershipResolver(session_factory=session_factory)
    email = f"provisioned-{uuid4().hex}@example.test"

    try:
        with pytest.raises(MembershipSelfMutationForbidden):
            await service.change_role(
                tenant_id=context.tenant_id,
                actor_id=context.actor_id,
                role="owner",
                membership_id=context.membership_id,
                member_role="member",
            )

        provisioned = await service.provision_member(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            role="owner",
            email=f" {email.upper()} ",
            member_role="member",
            request_id="membership-provision",
            correlation_id="membership-lifecycle",
        )
        replayed = await service.provision_member(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            role="owner",
            email=email,
            member_role="member",
        )
        assert replayed.membership_id == provisioned.membership_id
        assert provisioned.email == email

        binding = await binding_service.create_binding(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            role="owner",
            issuer="https://idp.example.test/",
            subject=f"subject-{uuid4().hex}",
            user_id=provisioned.user_id,
        )

        promoted = await service.change_role(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            role="owner",
            membership_id=provisioned.membership_id,
            member_role="owner",
        )
        assert promoted.role == "owner"
        demoted = await service.change_role(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            role="owner",
            membership_id=provisioned.membership_id,
            member_role="member",
        )
        assert demoted.role == "member"

        with pytest.raises(MembershipLastOwnerRequired):
            await service.deactivate_member(
                tenant_id=context.tenant_id,
                actor_id=uuid4(),
                role="owner",
                membership_id=context.membership_id,
            )

        deactivated = await service.deactivate_member(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            role="owner",
            membership_id=provisioned.membership_id,
            request_id="membership-deactivate",
            correlation_id="membership-lifecycle",
        )
        assert deactivated.is_active is False
        assert (
            await resolver.resolve_role(
                actor_id=provisioned.user_id,
                tenant_id=context.tenant_id,
            )
            is None
        )
        assert (
            await resolver.resolve_actor_id(
                tenant_id=context.tenant_id,
                issuer=binding.issuer,
                subject=binding.subject,
            )
            is None
        )

        # Repeated deactivation is idempotent.
        assert (
            await service.deactivate_member(
                tenant_id=context.tenant_id,
                actor_id=context.actor_id,
                role="owner",
                membership_id=provisioned.membership_id,
            )
        ).is_active is False

        reactivated = await service.activate_member(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            role="owner",
            membership_id=provisioned.membership_id,
            request_id="membership-reactivate",
            correlation_id="membership-lifecycle",
        )
        assert reactivated.is_active is True
        assert (
            await resolver.resolve_role(
                actor_id=provisioned.user_id,
                tenant_id=context.tenant_id,
            )
            == "member"
        )
        # Re-hiring does not silently restore an external login binding.
        assert (
            await resolver.resolve_actor_id(
                tenant_id=context.tenant_id,
                issuer=binding.issuer,
                subject=binding.subject,
            )
            is None
        )

        searched = await service.list_members(
            tenant_id=context.tenant_id,
            role="owner",
            query="provisioned-",
        )
        assert [member.membership_id for member in searched] == [provisioned.membership_id]
        other_members = await service.list_members(
            tenant_id=other_context.tenant_id,
            role="owner",
            query="provisioned-",
        )
        assert other_members == ()

        async with session_factory() as session:
            actions = (
                await session.scalars(
                    select(AuditEvent.action)
                    .where(
                        AuditEvent.tenant_id == context.tenant_id,
                        AuditEvent.resource_type == "tenant_membership",
                        AuditEvent.resource_id == provisioned.membership_id,
                    )
                    .order_by(AuditEvent.occurred_at.asc(), AuditEvent.id.asc())
                )
            ).all()
        assert actions == [
            "membership.provisioned",
            "membership.role_changed",
            "membership.role_changed",
            "membership.deactivated",
            "membership.reactivated",
        ]
    finally:
        await engine.dispose()
