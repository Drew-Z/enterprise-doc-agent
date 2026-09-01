from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from tests.agent.test_agent_run_integration import _seed_agent_context

from enterprise_doc_api.auth import DatabaseExternalMembershipResolver
from enterprise_doc_core.audit import AuditEvent
from enterprise_doc_core.config import DatabaseSettings
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.identity import Membership, User
from enterprise_doc_core.identity.service import (
    ExternalIdentityBindingConflict,
    ExternalIdentityBindingForbidden,
    ExternalIdentityBindingService,
    ExternalIdentityBindingTargetNotFound,
)

pytestmark = pytest.mark.integration


async def _add_member(session_factory: Any, *, tenant_id: UUID) -> UUID:
    user_id = uuid4()
    suffix = uuid4().hex
    async with session_factory.begin() as session:
        session.add(User(id=user_id, email=f"identity-{suffix}@example.test"))
        await session.flush()
        session.add(
            Membership(
                id=uuid4(),
                tenant_id=tenant_id,
                user_id=user_id,
                role="member",
                is_active=True,
            )
        )
    return user_id


async def test_external_identity_binding_round_trip_is_tenant_scoped_and_audited() -> None:
    engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(engine)
    context = await _seed_agent_context(session_factory)
    member_id = await _add_member(session_factory, tenant_id=context.tenant_id)
    other_context = await _seed_agent_context(session_factory)
    service = ExternalIdentityBindingService(session_factory=session_factory)
    resolver = DatabaseExternalMembershipResolver(session_factory=session_factory)
    issuer = " https://idp.example.test/ "
    subject = " subject-identity-123 "

    try:
        with pytest.raises(ExternalIdentityBindingForbidden):
            await service.create_binding(
                tenant_id=context.tenant_id,
                actor_id=member_id,
                role="member",
                issuer=issuer,
                subject=subject,
                user_id=member_id,
            )

        members = await service.list_active_members(
            tenant_id=context.tenant_id,
            role="owner",
            query="identity-",
        )
        assert {member.user_id for member in members} == {member_id}
        assert other_context.actor_id not in {member.user_id for member in members}

        created = await service.create_binding(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            role="owner",
            issuer=issuer,
            subject=subject,
            user_id=member_id,
            request_id="identity-binding-create",
            correlation_id="identity-binding-correlation",
        )
        assert created.issuer == issuer.strip()
        assert created.subject == subject.strip()
        assert created.user_id == member_id
        assert created.is_active is True

        assert (
            await resolver.resolve_actor_id(
                tenant_id=context.tenant_id,
                issuer=created.issuer,
                subject=created.subject,
            )
            == member_id
        )
        assert (
            await resolver.resolve_role(actor_id=member_id, tenant_id=context.tenant_id) == "member"
        )

        with pytest.raises(ExternalIdentityBindingConflict):
            await service.create_binding(
                tenant_id=context.tenant_id,
                actor_id=context.actor_id,
                role="owner",
                issuer=created.issuer,
                subject=created.subject,
                user_id=context.actor_id,
            )

        with pytest.raises(ExternalIdentityBindingTargetNotFound):
            await service.create_binding(
                tenant_id=context.tenant_id,
                actor_id=context.actor_id,
                role="owner",
                issuer="https://idp.example.test/other",
                subject="other-tenant-subject",
                user_id=other_context.actor_id,
            )

        bindings = await service.list_bindings(tenant_id=context.tenant_id, role="owner")
        assert [binding.binding_id for binding in bindings] == [created.binding_id]

        deactivated = await service.deactivate_binding(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            role="owner",
            binding_id=created.binding_id,
            request_id="identity-binding-deactivate",
            correlation_id="identity-binding-correlation",
        )
        assert deactivated.is_active is False
        assert (
            await resolver.resolve_actor_id(
                tenant_id=context.tenant_id,
                issuer=created.issuer,
                subject=created.subject,
            )
            is None
        )

        async with session_factory.begin() as session:
            membership = await session.scalar(
                select(Membership).where(
                    Membership.tenant_id == context.tenant_id,
                    Membership.user_id == member_id,
                )
            )
            assert membership is not None
            membership.is_active = False

        with pytest.raises(ExternalIdentityBindingTargetNotFound):
            await service.activate_binding(
                tenant_id=context.tenant_id,
                actor_id=context.actor_id,
                role="owner",
                binding_id=created.binding_id,
            )

        async with session_factory.begin() as session:
            membership = await session.scalar(
                select(Membership).where(
                    Membership.tenant_id == context.tenant_id,
                    Membership.user_id == member_id,
                )
            )
            assert membership is not None
            membership.is_active = True

        activated = await service.activate_binding(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            role="owner",
            binding_id=created.binding_id,
            request_id="identity-binding-activate",
            correlation_id="identity-binding-correlation",
        )
        assert activated.is_active is True
        assert (
            await resolver.resolve_actor_id(
                tenant_id=context.tenant_id,
                issuer=created.issuer,
                subject=created.subject,
            )
            == member_id
        )

        # Repeated activation is idempotent and does not emit a duplicate audit event.
        assert (
            await service.activate_binding(
                tenant_id=context.tenant_id,
                actor_id=context.actor_id,
                role="owner",
                binding_id=created.binding_id,
            )
        ).is_active is True

        async with session_factory() as session:
            actions = (
                await session.scalars(
                    select(AuditEvent.action)
                    .where(
                        AuditEvent.tenant_id == context.tenant_id,
                        AuditEvent.resource_id == created.binding_id,
                    )
                    .order_by(AuditEvent.occurred_at.asc())
                )
            ).all()
        assert actions == [
            "external_identity.binding.created",
            "external_identity.binding.deactivated",
            "external_identity.binding.activated",
        ]
    finally:
        await engine.dispose()
