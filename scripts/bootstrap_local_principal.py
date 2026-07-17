from __future__ import annotations

import argparse
import asyncio
import json

from enterprise_doc_api.auth.bootstrap import (
    BootstrapResult,
    bootstrap_principal,
    ensure_bootstrap_allowed,
)
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.db import (
    create_database_engine,
    create_session_factory,
    ensure_asyncio_compatibility,
)
from enterprise_doc_core.identity import MembershipRole


async def _run(args: argparse.Namespace) -> BootstrapResult:
    settings = ApiSettings()
    ensure_bootstrap_allowed(settings.app_env)
    engine = create_database_engine(settings.database)
    try:
        return await bootstrap_principal(
            settings=settings,
            session_factory=create_session_factory(engine),
            tenant_name=args.tenant_name,
            tenant_slug=args.tenant_slug,
            email=args.email,
            role=MembershipRole(args.role),
            quota_bytes=args.quota_bytes,
        )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create or refresh a local tenant principal and issue a short-lived JWT"
    )
    parser.add_argument("--tenant-name", default="Local Interview Tenant")
    parser.add_argument("--tenant-slug", default="local-interview")
    parser.add_argument("--email", default="developer@example.test")
    parser.add_argument(
        "--role",
        choices=[role.value for role in MembershipRole],
        default=MembershipRole.OWNER.value,
    )
    parser.add_argument("--quota-bytes", type=int, default=10 * 1024**3)
    args = parser.parse_args()

    ensure_asyncio_compatibility()
    result = asyncio.run(_run(args))
    print(
        json.dumps(
            {
                "tenantId": str(result.tenant_id),
                "actorId": str(result.actor_id),
                "role": result.role,
                "token": result.token,
            },
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
