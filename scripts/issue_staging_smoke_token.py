from __future__ import annotations

import argparse
import asyncio
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_api.auth.jwt import JwtTokenCodec
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.config import AppEnvironment
from enterprise_doc_core.db import (
    create_database_engine,
    create_session_factory,
    ensure_asyncio_compatibility,
)
from enterprise_doc_core.identity import Membership, Tenant, User


class StagingTokenNotAllowed(RuntimeError):
    pass


class ActiveSmokePrincipalRequired(RuntimeError):
    pass


def ensure_staging(environment: AppEnvironment) -> None:
    if environment is not AppEnvironment.STAGING:
        raise StagingTokenNotAllowed("smoke token issuance is allowed only in staging")


async def issue_staging_smoke_token(
    *,
    settings: ApiSettings,
    session_factory: async_sessionmaker[AsyncSession],
    tenant_id: UUID,
    actor_id: UUID,
) -> str:
    ensure_staging(settings.app_env)
    statement = (
        select(Membership.role)
        .join(Tenant, Tenant.id == Membership.tenant_id)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.tenant_id == tenant_id,
            Membership.user_id == actor_id,
            Membership.is_active.is_(True),
            Tenant.is_active.is_(True),
            User.is_active.is_(True),
        )
    )
    async with session_factory() as session:
        role = await session.scalar(statement)
    if role is None:
        raise ActiveSmokePrincipalRequired(
            "the requested smoke principal has no active tenant membership"
        )
    return JwtTokenCodec(settings.auth).issue_local_token(
        tenant_id=tenant_id,
        actor_id=actor_id,
    )


async def _run(args: argparse.Namespace) -> str:
    settings = ApiSettings(_env_file=None)
    ensure_staging(settings.app_env)
    engine = create_database_engine(settings.database)
    try:
        return await issue_staging_smoke_token(
            settings=settings,
            session_factory=create_session_factory(engine),
            tenant_id=args.tenant_id,
            actor_id=args.actor_id,
        )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Issue a staging-only JWT for an existing active smoke principal"
    )
    parser.add_argument("--tenant-id", type=UUID, required=True)
    parser.add_argument("--actor-id", type=UUID, required=True)
    args = parser.parse_args()

    ensure_asyncio_compatibility()
    print(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
