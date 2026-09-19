"""Local-only operator interface. This command never accepts a user identity or issues a JWT."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Never
from uuid import UUID

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
from enterprise_doc_core.admission.errors import (
    AdmissionError,
    AdmissionForbidden,
    AdmissionInvalid,
)
from enterprise_doc_core.admission.service import TenantAdmissionService
from enterprise_doc_core.config import AppEnvironment, FoundationSettings
from enterprise_doc_core.db import (
    create_database_engine,
    create_session_factory,
    ensure_asyncio_compatibility,
)


class _SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        # argparse normally echoes unknown arguments, which may include an
        # accidentally supplied credential. The CLI has no secret argument.
        raise AdmissionInvalid()


def build_parser() -> argparse.ArgumentParser:
    parser = _SafeParser(description="Preview or manage local tenant admission grants")
    commands = parser.add_subparsers(dest="command", required=True)
    for operation in ("issue", "show", "revoke"):
        command = commands.add_parser(operation)
        command.add_argument("--operator", required=True)
        command.add_argument("--reason", required=True)
        if operation != "show":
            command.add_argument("--execute", action="store_true")
        if operation == "issue":
            command.add_argument("--email", required=True)
            command.add_argument("--issuer", required=True)
            command.add_argument("--expires-at", required=True)
            command.add_argument("--quota-bytes", type=int, required=True)
            command.add_argument("--seat-limit", type=int, required=True)
            command.add_argument("--credential-file", type=Path, required=True)
        else:
            command.add_argument("--grant-id", type=UUID, required=True)
    return parser


def _require_local(settings: FoundationSettings) -> None:
    if settings.app_env not in {AppEnvironment.LOCAL, AppEnvironment.TEST}:
        raise AdmissionForbidden()
    if make_url(settings.database.url.get_secret_value()).host not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise AdmissionForbidden()


async def run_command(
    args: argparse.Namespace, settings: FoundationSettings
) -> tuple[int, dict[str, object]]:
    _require_local(settings)
    operator = require_operator(
        PlatformAdmissionOperator(operator_id=args.operator, reason=args.reason)
    )
    request = None
    if args.command == "issue":
        request = AdmissionGrantRequest(
            recipient_email=args.email,
            issuer=args.issuer,
            expires_at=args.expires_at,
            quota_bytes=args.quota_bytes,
            seat_limit=args.seat_limit,
        )
        now = datetime.now(UTC)
        if not now < request.expires_at <= now + timedelta(days=30):
            raise AdmissionInvalid()
        validate_credential_path(args.credential_file)
    if args.command != "show" and not args.execute:
        result: dict[str, object] = {
            "status": "preview",
            "operation": args.command,
            "operator": operator.operator_id,
            "reason": operator.reason,
        }
        if request is not None:
            result.update(
                request=request.model_dump(mode="json"), credentialFile=str(args.credential_file)
            )
        else:
            result["grantId"] = str(args.grant_id)
        return 0, result

    credential = None
    context: dict[str, object] = {"operation": args.command}
    if request is not None:
        credential = prepare_admission_credential()
        context.update(grantId=str(credential.grant_id), credentialFile=str(args.credential_file))
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
    try:
        engine = create_database_engine(settings.database)
        try:
            service = TenantAdmissionService(
                session_factory=create_session_factory(engine),
                trusted_issuers=frozenset({request.issuer}) if request is not None else frozenset(),
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
            else "admission_operation_failed",
        }
    return 0, {**context, "status": "confirmed", "grant": asdict(snapshot)}


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
            "code": error.code if isinstance(error, AdmissionError) else "admission_invalid",
        }
    print(json.dumps(result, ensure_ascii=False, default=str, separators=(",", ":")))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
