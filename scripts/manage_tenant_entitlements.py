"""Local operator commands for explicit, append-only commercial entitlement periods."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from dataclasses import asdict
from typing import Never
from uuid import UUID

from sqlalchemy.engine import make_url

from enterprise_doc_core.billing.administration import EntitlementAdministrationService
from enterprise_doc_core.billing.administration_contracts import (
    EntitlementConfiguration,
    PlatformEntitlementOperator,
    require_entitlement_operator,
)
from enterprise_doc_core.billing.errors import UsageError
from enterprise_doc_core.config import AppEnvironment, FoundationSettings
from enterprise_doc_core.db import (
    create_database_engine,
    create_session_factory,
    ensure_asyncio_compatibility,
)


class _SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        # Unknown arguments may contain accidentally supplied credentials.
        raise UsageError("entitlement_invalid_arguments")


def build_parser() -> argparse.ArgumentParser:
    parser = _SafeParser(description="Preview or manage local commercial entitlement periods")
    commands = parser.add_subparsers(dest="command", required=True)
    for operation in ("configure", "list", "show"):
        command = commands.add_parser(operation)
        command.add_argument("--tenant-id", type=UUID, required=True)
        command.add_argument("--operator", required=True)
        command.add_argument("--reason", required=True)
        if operation in {"configure", "show"}:
            command.add_argument("--entitlement-id", type=UUID, required=True)
        if operation == "configure":
            command.add_argument("--expected-version", type=int, required=True)
            command.add_argument("--plan-code", required=True)
            command.add_argument("--period-start", required=True)
            command.add_argument("--period-end", required=True)
            command.add_argument(
                "--request-limit", dest="provider_request_limit", type=int, required=True
            )
            command.add_argument("--execute", action="store_true")
        elif operation == "list":
            command.add_argument("--limit", type=int, default=20)
    return parser


def _require_local(settings: FoundationSettings) -> None:
    url = make_url(settings.database.url.get_secret_value())
    if (
        settings.app_env not in {AppEnvironment.LOCAL, AppEnvironment.TEST}
        or url.host not in {"127.0.0.1", "localhost", "::1"}
        or {"host", "hostaddr", "service", "servicefile"}.intersection(url.query)
    ):
        raise UsageError("entitlement_local_only")


async def run_command(
    args: argparse.Namespace, settings: FoundationSettings
) -> tuple[int, dict[str, object]]:
    _require_local(settings)
    operator = require_entitlement_operator(PlatformEntitlementOperator(args.operator, args.reason))
    context: dict[str, object] = {"operation": args.command, "tenantId": str(args.tenant_id)}
    configuration = None
    if args.command in {"configure", "show"}:
        context["entitlementId"] = str(args.entitlement_id)
    if args.command == "configure":
        configuration = EntitlementConfiguration(
            entitlement_id=args.entitlement_id,
            expected_version=args.expected_version,
            plan_code=args.plan_code,
            period_start=args.period_start,
            period_end=args.period_end,
            provider_request_limit=args.provider_request_limit,
        )
        if not args.execute:
            return 0, {
                **context,
                "status": "preview",
                "databaseValidated": False,
                "operator": operator.operator_id,
                "reason": operator.reason,
                "request": configuration.model_dump(mode="json"),
            }
    try:
        engine = create_database_engine(settings.database)
        try:
            service = EntitlementAdministrationService(
                session_factory=create_session_factory(engine)
            )
            if configuration is not None:
                configured = await service.configure(
                    tenant_id=args.tenant_id, operator=operator, configuration=configuration
                )
                output: dict[str, object] = {
                    "entitlement": asdict(configured.entitlement),
                    "replayed": configured.replayed,
                }
            elif args.command == "show":
                shown = await service.show(
                    tenant_id=args.tenant_id, entitlement_id=args.entitlement_id, operator=operator
                )
                output = {"entitlement": asdict(shown)}
            else:
                rows = await service.list(
                    tenant_id=args.tenant_id, operator=operator, limit=args.limit
                )
                output = {
                    "entitlements": [asdict(row) for row in rows],
                    "latestVersion": rows[0].version if rows else 0,
                    "limit": args.limit,
                }
        finally:
            await engine.dispose()
    except Exception as error:
        return 1, {
            **context,
            "status": "not_confirmed" if args.command == "configure" else "failed",
            "code": error.code if isinstance(error, UsageError) else "entitlement_operation_failed",
        }
    return 0, {**context, "status": "confirmed", **output}


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        settings = FoundationSettings()
        ensure_asyncio_compatibility()
        status, result = asyncio.run(run_command(args, settings))
    except Exception as error:
        status = 2
        result = {
            "status": "failed",
            "code": error.code
            if isinstance(error, UsageError)
            else "entitlement_invalid_configuration",
        }
    print(json.dumps(result, ensure_ascii=False, default=str, separators=(",", ":")))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
