"""Private administrative CLI; the host/cluster and DB credentials authorize its caller.

Operator labels provide attribution, not authorization. There is no HTTP endpoint
or tenant-token authentication, and this module never loads a working-directory .env.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Never
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from enterprise_doc_core.admission.contracts import (
    AdmissionGrantRequest,
    PlatformAdmissionOperator,
    prepare_admission_credential,
    require_operator,
)
from enterprise_doc_core.admission.credential_file import (
    validate_credential_path,
    write_private_credential,
)
from enterprise_doc_core.admission.errors import AdmissionError
from enterprise_doc_core.admission.service import TenantAdmissionService
from enterprise_doc_core.billing.administration import EntitlementAdministrationService
from enterprise_doc_core.billing.administration_contracts import (
    EntitlementConfiguration,
    PlatformEntitlementOperator,
    ProductQuotaConfiguration,
    require_entitlement_operator,
)
from enterprise_doc_core.billing.errors import UsageError
from enterprise_doc_core.config import AppEnvironment, DatabaseSettings
from enterprise_doc_core.db import (
    create_database_engine,
    create_session_factory,
    ensure_asyncio_compatibility,
)

OPERATION_TIMEOUT_SECONDS = 30


class OperationsError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class RequiredDatabaseSettings(DatabaseSettings):
    url: SecretStr


class OperatorBrowserAuth(BaseModel):
    enabled: bool = False
    issuer: str | None = None


class OperationsSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=None, env_nested_delimiter="__", extra="ignore", hide_input_in_errors=True
    )

    app_env: AppEnvironment
    database: RequiredDatabaseSettings
    browser_auth: OperatorBrowserAuth = Field(default_factory=OperatorBrowserAuth)


class _SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise OperationsError("operations_invalid_arguments")


def build_parser() -> argparse.ArgumentParser:
    parser = _SafeParser(
        description="Private platform admission and entitlement operations", allow_abbrev=False
    )
    parser.add_argument("--environment", choices=("staging", "production"), required=True)
    parser.add_argument("--database-host", required=True)
    parser.add_argument("--database-port", type=int, default=5432)
    parser.add_argument("--database-name", required=True)
    parser.add_argument("--operator", required=True)
    parser.add_argument("--reason", required=True)
    resources = parser.add_subparsers(dest="resource", required=True)
    admission = resources.add_parser("admission", allow_abbrev=False)
    commands = admission.add_subparsers(dest="command", required=True)
    issue = commands.add_parser("issue", allow_abbrev=False)
    issue.add_argument("--email", required=True)
    issue.add_argument("--expires-at", required=True)
    issue.add_argument("--quota-bytes", type=int, required=True)
    issue.add_argument("--seat-limit", type=int, required=True)
    issue.add_argument("--credential-file", type=Path, required=True)
    issue.add_argument("--execute", action="store_true")
    for operation in ("show", "revoke"):
        command = commands.add_parser(operation, allow_abbrev=False)
        command.add_argument("--grant-id", type=UUID, required=True)
        if operation == "revoke":
            command.add_argument("--execute", action="store_true")
    entitlement = resources.add_parser("entitlement", allow_abbrev=False)
    periods = entitlement.add_subparsers(dest="command", required=True)
    for operation in ("configure", "configure-products", "show", "list"):
        command = periods.add_parser(operation, allow_abbrev=False)
        command.add_argument("--tenant-id", type=UUID, required=True)
        if operation in {"configure", "configure-products", "show"}:
            command.add_argument("--entitlement-id", type=UUID, required=True)
        if operation in {"configure", "configure-products"}:
            command.add_argument("--expected-version", type=int, required=True)
            command.add_argument(
                "--agent-task-limit",
                type=int,
                default=0,
                required=operation == "configure-products",
            )
            command.add_argument(
                "--document-bytes-limit",
                type=int,
                default=0,
                required=operation == "configure-products",
            )
            command.add_argument("--execute", action="store_true")
        if operation == "configure":
            command.add_argument("--plan-code", required=True)
            command.add_argument("--period-start", required=True)
            command.add_argument("--period-end", required=True)
            command.add_argument("--request-limit", type=int, required=True)
        elif operation == "list":
            command.add_argument("--limit", type=int, default=20)
    return parser


def require_target(args: argparse.Namespace, settings: OperationsSettings) -> dict[str, object]:
    if settings.app_env not in {AppEnvironment.STAGING, AppEnvironment.PRODUCTION}:
        raise OperationsError("operations_environment_forbidden")
    url = make_url(settings.database.url.get_secret_value())
    if url.drivername != "postgresql+psycopg" or not url.host or not url.database:
        raise OperationsError("operations_invalid_database")
    overrides = {"host", "hostaddr", "port", "dbname", "service", "servicefile"}
    if overrides.intersection(key.lower() for key in url.query) or any(
        os.environ.get(key) for key in ("PGHOSTADDR", "PGPORT", "PGSERVICE", "PGSERVICEFILE")
    ):
        raise OperationsError("operations_database_override_forbidden")
    if any(character.isspace() or character in ",/\\@%?#" for character in url.host):
        raise OperationsError("operations_invalid_database")
    target: dict[str, object] = {
        "environment": settings.app_env.value,
        "host": url.host,
        "port": url.port or 5432,
        "database": url.database,
    }
    if target != {
        "environment": args.environment,
        "host": args.database_host,
        "port": args.database_port,
        "database": args.database_name,
    }:
        raise OperationsError("operations_target_mismatch")
    return target


def configured_issuer(settings: OperationsSettings) -> str:
    issuer = settings.browser_auth.issuer or ""
    parsed = urlsplit(issuer)
    if (
        not settings.browser_auth.enabled
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "?" in issuer
        or "#" in issuer
        or "\\" in issuer
        or any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in issuer
        )
    ):
        raise OperationsError("operations_browser_identity_unavailable")
    # Accessing port also validates malformed/non-numeric ports before file creation.
    _ = parsed.port
    return issuer


async def run_admission(
    args: argparse.Namespace,
    settings: OperationsSettings,
    operator: PlatformAdmissionOperator,
    context: dict[str, object],
) -> tuple[int, dict[str, object]]:
    request = None
    credential = None
    if args.command == "issue":
        request = AdmissionGrantRequest(
            recipient_email=args.email,
            issuer=configured_issuer(settings),
            expires_at=args.expires_at,
            quota_bytes=args.quota_bytes,
            seat_limit=args.seat_limit,
        )
        now = datetime.now(UTC)
        if not now < request.expires_at <= now + timedelta(days=30):
            raise OperationsError("admission_invalid")
        validate_credential_path(args.credential_file)
        context["credentialFile"] = str(args.credential_file)
        if not args.execute:
            return 0, {
                **context,
                "status": "preview",
                "databaseValidated": False,
                "request": request.model_dump(mode="json"),
            }
        credential = prepare_admission_credential()
        context["grantId"] = str(credential.grant_id)
        try:
            write_private_credential(args.credential_file, credential)
        except OSError:
            return 1, {
                **context,
                "status": "file_failed",
                "code": "credential_file_write_failed",
                "databaseWriteAttempted": False,
            }
    else:
        context["grantId"] = str(args.grant_id)
        if args.command == "revoke" and not args.execute:
            return 0, {**context, "status": "preview", "databaseValidated": False}

    try:
        async with asyncio.timeout(OPERATION_TIMEOUT_SECONDS):
            engine = create_database_engine(settings.database)
            try:
                service = TenantAdmissionService(
                    session_factory=create_session_factory(engine),
                    trusted_issuers=frozenset({request.issuer}) if request else frozenset(),
                )
                if request is not None and credential is not None:
                    snapshot = await service.issue(
                        operator=operator, request=request, credential=credential
                    )
                elif args.command == "revoke":
                    snapshot = await service.revoke(operator=operator, grant_id=args.grant_id)
                else:
                    snapshot = await service.show(operator=operator, grant_id=args.grant_id)
            finally:
                await engine.dispose()
    except Exception as error:
        return 1, {
            **context,
            "status": "failed" if args.command == "show" else "not_confirmed",
            "code": error.code
            if isinstance(error, AdmissionError)
            else "operations_timeout"
            if isinstance(error, TimeoutError)
            else "admission_operation_failed",
        }
    return 0, {**context, "status": "confirmed", "grant": asdict(snapshot)}


async def run_entitlement(
    args: argparse.Namespace,
    settings: OperationsSettings,
    context: dict[str, object],
) -> tuple[int, dict[str, object]]:
    operator = require_entitlement_operator(PlatformEntitlementOperator(args.operator, args.reason))
    context["tenantId"] = str(args.tenant_id)
    configuration = None
    products = None
    if args.command in {"configure", "configure-products", "show"}:
        context["entitlementId"] = str(args.entitlement_id)
    if args.command == "configure":
        configuration = EntitlementConfiguration(
            entitlement_id=args.entitlement_id,
            expected_version=args.expected_version,
            plan_code=args.plan_code,
            period_start=args.period_start,
            period_end=args.period_end,
            provider_request_limit=args.request_limit,
            agent_task_limit=args.agent_task_limit,
            document_bytes_limit=args.document_bytes_limit,
        )
        if not args.execute:
            return 0, {
                **context,
                "status": "preview",
                "databaseValidated": False,
                "request": configuration.model_dump(mode="json"),
            }
    elif args.command == "configure-products":
        products = ProductQuotaConfiguration(
            entitlement_id=args.entitlement_id,
            expected_version=args.expected_version,
            agent_task_limit=args.agent_task_limit,
            document_bytes_limit=args.document_bytes_limit,
        )
        if not args.execute:
            return 0, {
                **context,
                "status": "preview",
                "databaseValidated": False,
                "request": products.model_dump(mode="json"),
            }
    elif args.command == "list" and not 1 <= args.limit <= 100:
        raise UsageError("entitlement_invalid_limit")
    try:
        async with asyncio.timeout(OPERATION_TIMEOUT_SECONDS):
            engine = create_database_engine(settings.database)
            try:
                service = EntitlementAdministrationService(
                    session_factory=create_session_factory(engine)
                )
                if products is not None:
                    configured = await service.configure_products(
                        tenant_id=args.tenant_id, operator=operator, configuration=products
                    )
                    output: dict[str, object] = {
                        "entitlement": asdict(configured.entitlement),
                        "replayed": configured.replayed,
                    }
                elif configuration is not None:
                    configured = await service.configure(
                        tenant_id=args.tenant_id, operator=operator, configuration=configuration
                    )
                    output = {
                        "entitlement": asdict(configured.entitlement),
                        "replayed": configured.replayed,
                    }
                elif args.command == "show":
                    shown = await service.show(
                        tenant_id=args.tenant_id,
                        entitlement_id=args.entitlement_id,
                        operator=operator,
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
            "status": "not_confirmed" if args.command.startswith("configure") else "failed",
            "code": error.code
            if isinstance(error, UsageError)
            else "operations_timeout"
            if isinstance(error, TimeoutError)
            else "entitlement_operation_failed",
        }
    return 0, {**context, "status": "confirmed", **output}


async def run_command(
    args: argparse.Namespace, settings: OperationsSettings
) -> tuple[int, dict[str, object]]:
    target = require_target(args, settings)
    operator = require_operator(PlatformAdmissionOperator(args.operator, args.reason))
    context: dict[str, object] = {
        "operation": f"{args.resource}.{args.command}",
        "target": target,
        "operator": operator.operator_id,
        "reason": operator.reason,
    }
    if args.resource == "admission":
        return await run_admission(args, settings, operator, context)
    return await run_entitlement(args, settings, context)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        settings = OperationsSettings()
        ensure_asyncio_compatibility()
        status, result = asyncio.run(run_command(args, settings))
    except Exception as error:
        status = 2
        result = {
            "status": "failed",
            "code": error.code
            if isinstance(error, (OperationsError, AdmissionError, UsageError))
            else "operations_invalid_configuration",
        }
    print(json.dumps(result, ensure_ascii=False, default=str, separators=(",", ":")))
    return status
