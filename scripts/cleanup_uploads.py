from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence

from pydantic import ValidationError

from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.config import UploadSettings
from enterprise_doc_core.db import (
    create_database_engine,
    create_session_factory,
    ensure_asyncio_compatibility,
)
from enterprise_doc_core.object_store import Boto3MultipartObjectStore
from enterprise_doc_core.uploads import UploadCleanupReport, UploadCleanupService


class CleanupArgumentError(ValueError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Clean expired and failed multipart upload state in one bounded batch"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--expiry-grace-seconds", type=int)
    parser.add_argument("--completing-grace-seconds", type=int)
    parser.add_argument("--orphan-grace-seconds", type=int)
    parser.add_argument("--claim-ttl-seconds", type=int)
    return parser


def _validated_upload_settings(
    *,
    settings: UploadSettings,
    args: argparse.Namespace,
) -> UploadSettings:
    overrides = {
        key: value
        for key, value in {
            "cleanup_batch_size": args.batch_size,
            "cleanup_expiry_grace_seconds": args.expiry_grace_seconds,
            "cleanup_completing_grace_seconds": args.completing_grace_seconds,
            "cleanup_orphan_grace_seconds": args.orphan_grace_seconds,
            "cleanup_claim_ttl_seconds": args.claim_ttl_seconds,
        }.items()
        if value is not None
    }
    try:
        return UploadSettings.model_validate(settings.model_dump() | overrides)
    except ValidationError as error:
        raise CleanupArgumentError("cleanup overrides are outside the allowed range") from error


async def _run(args: argparse.Namespace) -> UploadCleanupReport:
    try:
        settings = ApiSettings()
    except Exception as error:
        return UploadCleanupReport.empty(dry_run=args.dry_run).with_exception(error)
    upload_settings = _validated_upload_settings(settings=settings.upload, args=args)

    engine = None
    object_store = None
    report: UploadCleanupReport | None = None
    try:
        engine = create_database_engine(settings.database)
        object_store = Boto3MultipartObjectStore(settings=settings.object_store)
        service = UploadCleanupService(
            session_factory=create_session_factory(engine),
            object_store=object_store,
            documents_bucket=settings.object_store.documents_bucket,
            settings=upload_settings,
        )
        report = await service.run(dry_run=args.dry_run)
    except Exception as error:
        report = (report or UploadCleanupReport.empty(dry_run=args.dry_run)).with_exception(error)
    finally:
        if object_store is not None:
            try:
                await object_store.close()
            except Exception as error:
                report = (report or UploadCleanupReport.empty(dry_run=args.dry_run)).with_exception(
                    error
                )
        if engine is not None:
            try:
                await engine.dispose()
            except Exception as error:
                report = (report or UploadCleanupReport.empty(dry_run=args.dry_run)).with_exception(
                    error
                )
    return report or UploadCleanupReport.empty(dry_run=args.dry_run)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    ensure_asyncio_compatibility()
    try:
        report = asyncio.run(_run(args))
    except CleanupArgumentError as error:
        parser.error(str(error))
    print(json.dumps(report.to_dict(), separators=(",", ":"), sort_keys=True))
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
