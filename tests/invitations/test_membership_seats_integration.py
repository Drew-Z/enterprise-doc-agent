from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from enterprise_doc_api.auth.bootstrap import bootstrap_principal
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.identity.membership_service import MembershipAdministrationService
from enterprise_doc_core.identity.models import (
    ExternalIdentityBinding,
    Membership,
    MembershipRole,
    Tenant,
    User,
)
from enterprise_doc_core.identity.scim_service import ScimProvisioningService
from enterprise_doc_core.identity.seats import MembershipSeatLimitReached, membership_seats

from .conftest import InvitationDatabase
from .support import ISSUER, tenant_owner

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


async def test_provision_cannot_exceed_initial_seat_limit(
    invitation_db: InvitationDatabase,
) -> None:
    owner = await tenant_owner(invitation_db, seats=1)
    service = MembershipAdministrationService(session_factory=invitation_db.sessions)
    failure: Exception | None = None
    try:
        await service.provision_member(
            tenant_id=owner.tenant_id,
            actor_id=owner.user_id,
            role="owner",
            email="no-seat@example.test",
            member_role="member",
        )
    except Exception as error:
        failure = error
    assert getattr(failure, "code", None) == "membership_seat_limit_reached"
    async with invitation_db.sessions() as session:
        assert (
            await session.scalar(
                select(func.count(Membership.id)).where(
                    Membership.tenant_id == owner.tenant_id,
                    Membership.is_active.is_(True),
                )
            )
            == 1
        )
        assert (
            await session.scalar(select(User.id).where(User.email == "no-seat@example.test"))
            is None
        )


@pytest.mark.parametrize(
    "writer",
    [
        "activate",
        "provision_restore",
        "scim_create",
        "scim_restore",
        "bootstrap_create",
        "bootstrap_restore",
    ],
)
async def test_other_writers_cannot_bypass_seats(
    invitation_db: InvitationDatabase,
    writer: str,
) -> None:
    owner = await tenant_owner(invitation_db, seats=1)
    user_id, membership_id = uuid4(), uuid4()
    email, subject = "other-seat@example.test", "other-seat"
    restore = writer == "activate" or writer.endswith("_restore")
    if restore:
        async with invitation_db.sessions.begin() as session:
            session.add(User(id=user_id, email=email))
            await session.flush()
            session.add(
                Membership(
                    id=membership_id,
                    tenant_id=owner.tenant_id,
                    user_id=user_id,
                    role="member",
                    is_active=False,
                )
            )
            session.add(
                ExternalIdentityBinding(
                    tenant_id=owner.tenant_id,
                    user_id=user_id,
                    issuer=ISSUER,
                    subject=subject,
                    is_active=False,
                )
            )
    failure: Exception | None = None
    try:
        if writer == "provision_restore":
            await MembershipAdministrationService(
                session_factory=invitation_db.sessions
            ).provision_member(
                tenant_id=owner.tenant_id,
                actor_id=owner.user_id,
                role="owner",
                email=email,
                member_role="member",
            )
        elif writer == "activate":
            await MembershipAdministrationService(
                session_factory=invitation_db.sessions,
            ).activate_member(
                tenant_id=owner.tenant_id,
                actor_id=owner.user_id,
                role="owner",
                membership_id=membership_id,
            )
        elif writer.startswith("scim"):
            await ScimProvisioningService(session_factory=invitation_db.sessions).sync_user(
                tenant_id=owner.tenant_id,
                issuer=ISSUER,
                subject=subject,
                email=email,
                role="member",
                is_active=True,
            )
        else:
            await bootstrap_principal(
                settings=ApiSettings(),
                session_factory=invitation_db.sessions,
                tenant_name="邀请测试企业",
                tenant_slug=f"t-{owner.tenant_id.hex}",
                email=email,
                role=MembershipRole.MEMBER,
                quota_bytes=1000000,
            )
    except Exception as error:
        failure = error
    assert getattr(failure, "code", None) == "membership_seat_limit_reached"
    async with invitation_db.sessions() as session:
        assert (
            await session.scalar(
                select(func.count(Membership.id)).where(
                    Membership.tenant_id == owner.tenant_id,
                    Membership.is_active.is_(True),
                )
            )
            == 1
        )
        if restore:
            assert (
                await session.scalar(
                    select(Membership.is_active).where(
                        Membership.id == membership_id,
                    )
                )
                is False
            )
            assert (
                await session.scalar(
                    select(ExternalIdentityBinding.is_active).where(
                        ExternalIdentityBinding.user_id == user_id,
                    )
                )
                is False
            )
        else:
            assert await session.scalar(select(User.id).where(User.email == email)) is None


@pytest.mark.parametrize("writer", ["provision", "activate", "scim", "bootstrap"])
async def test_already_active_members_do_not_consume_another_seat(
    invitation_db: InvitationDatabase,
    writer: str,
) -> None:
    owner = await tenant_owner(invitation_db, seats=1)
    service = MembershipAdministrationService(session_factory=invitation_db.sessions)
    if writer == "provision":
        await service.provision_member(
            tenant_id=owner.tenant_id,
            actor_id=owner.user_id,
            role="owner",
            email=owner.email,
            member_role="owner",
        )
    elif writer == "activate":
        await service.activate_member(
            tenant_id=owner.tenant_id,
            actor_id=owner.user_id,
            role="owner",
            membership_id=owner.membership_id,
        )
    elif writer == "scim":
        await ScimProvisioningService(session_factory=invitation_db.sessions).sync_user(
            tenant_id=owner.tenant_id,
            issuer=ISSUER,
            subject=owner.subject,
            email=owner.email,
            role="owner",
            is_active=True,
        )
    else:
        await bootstrap_principal(
            settings=ApiSettings(),
            session_factory=invitation_db.sessions,
            tenant_name="邀请测试企业",
            tenant_slug=f"t-{owner.tenant_id.hex}",
            email=owner.email,
            role=MembershipRole.OWNER,
            quota_bytes=1000000,
        )
    async with invitation_db.sessions() as session:
        seats = await membership_seats(session, owner.tenant_id)
        assert (seats.active, seats.limit, seats.remaining) == (1, 1, 0)
        assert await session.scalar(select(func.count(Membership.id))) == 1


@pytest.mark.parametrize("writer", ["provision", "activate", "scim", "bootstrap"])
async def test_legacy_enterprise_retains_member_write_behavior(
    invitation_db: InvitationDatabase,
    writer: str,
) -> None:
    owner = await tenant_owner(invitation_db, seats=None)
    service = MembershipAdministrationService(session_factory=invitation_db.sessions)
    email = "legacy-member@example.test"
    if writer == "provision":
        await service.provision_member(
            tenant_id=owner.tenant_id,
            actor_id=owner.user_id,
            role="owner",
            email=email,
            member_role="member",
        )
    elif writer == "activate":
        user_id, member_id = uuid4(), uuid4()
        async with invitation_db.sessions.begin() as session:
            session.add(User(id=user_id, email=email))
            await session.flush()
            session.add(
                Membership(
                    id=member_id,
                    tenant_id=owner.tenant_id,
                    user_id=user_id,
                    role="member",
                    is_active=False,
                )
            )
        await service.activate_member(
            tenant_id=owner.tenant_id, actor_id=owner.user_id, role="owner", membership_id=member_id
        )
    elif writer == "scim":
        await ScimProvisioningService(session_factory=invitation_db.sessions).sync_user(
            tenant_id=owner.tenant_id,
            issuer=ISSUER,
            subject="legacy-member",
            email=email,
            role="member",
            is_active=True,
        )
    else:
        await bootstrap_principal(
            settings=ApiSettings(),
            session_factory=invitation_db.sessions,
            tenant_name="邀请测试企业",
            tenant_slug=f"t-{owner.tenant_id.hex}",
            email=email,
            role=MembershipRole.MEMBER,
            quota_bytes=1000000,
        )
    async with invitation_db.sessions() as session:
        seats = await membership_seats(session, owner.tenant_id)
        assert (seats.active, seats.limit, seats.remaining) == (2, None, None)


async def test_inactive_user_still_occupies_a_seat_until_membership_deactivation(
    invitation_db: InvitationDatabase,
) -> None:
    owner = await tenant_owner(invitation_db, seats=2)
    user_id, member_id = uuid4(), uuid4()
    async with invitation_db.sessions.begin() as session:
        session.add(User(id=user_id, email="disabled-user@example.test", is_active=False))
        await session.flush()
        session.add(
            Membership(
                id=member_id,
                tenant_id=owner.tenant_id,
                user_id=user_id,
                role="member",
                is_active=True,
            )
        )
    service = MembershipAdministrationService(session_factory=invitation_db.sessions)
    with pytest.raises(MembershipSeatLimitReached):
        await service.provision_member(
            tenant_id=owner.tenant_id,
            actor_id=owner.user_id,
            role="owner",
            email="replacement@example.test",
            member_role="member",
        )
    await service.deactivate_member(
        tenant_id=owner.tenant_id, actor_id=owner.user_id, role="owner", membership_id=member_id
    )
    result = await service.provision_member(
        tenant_id=owner.tenant_id,
        actor_id=owner.user_id,
        role="owner",
        email="replacement@example.test",
        member_role="member",
    )
    assert result.is_active
    async with invitation_db.sessions() as session:
        assert (await membership_seats(session, owner.tenant_id)).active == 2
        assert await session.scalar(select(User.is_active).where(User.id == user_id)) is False


async def test_bootstrap_cli_returns_safe_seat_error_and_rolls_back_changes(
    invitation_db: InvitationDatabase,
) -> None:
    owner = await tenant_owner(invitation_db, seats=1)
    environment = dict(os.environ)
    url = make_url(ApiSettings().database.url.get_secret_value())
    environment["DATABASE__URL"] = url.update_query_dict(
        {
            "options": f"-csearch_path={invitation_db.schema},public",
        }
    ).render_as_string(hide_password=False)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = await asyncio.to_thread(
        subprocess.run,
        [
            sys.executable,
            "-X",
            "utf8",
            "-B",
            str(ROOT / "scripts/bootstrap_local_principal.py"),
            "--tenant-name",
            "Should roll back",
            "--tenant-slug",
            f"t-{owner.tenant_id.hex}",
            "--email",
            "cli-no-seat@example.test",
            "--role",
            "member",
            "--quota-bytes",
            "2000000",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        timeout=30,
        check=False,
    )
    code, output_size = result.returncode, len(result.stdout)
    safe_error = result.stderr.strip() == b'{"error":"membership_seat_limit_reached"}'
    assert (code, output_size, safe_error) == (1, 0, True)
    async with invitation_db.sessions() as session:
        tenant = await session.get(Tenant, owner.tenant_id)
        assert tenant is not None
        assert (tenant.name, tenant.quota_bytes) == ("邀请测试企业", 1000000)
        assert (
            await session.scalar(select(User.id).where(User.email == "cli-no-seat@example.test"))
            is None
        )
