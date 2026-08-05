from __future__ import annotations

import argparse
import json
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from enterprise_doc_core.config import FoundationSettings
from enterprise_doc_core.db import ensure_asyncio_compatibility
from enterprise_doc_core.object_store import create_s3_client
from enterprise_doc_core.recovery.cli_common import (
    read_manifest,
    run_reference_load,
    write_private_record,
)
from enterprise_doc_core.recovery.object_store import (
    database_name_from_url,
    endpoint_host_from_url,
    parse_snapshot_manifest,
    restore_snapshot,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Restore a verified object snapshot into an isolated object-store prefix"
    )
    parser.add_argument("--manifest-path", type=Path, required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--restore-id", required=True)
    parser.add_argument("--expected-database-name", required=True)
    parser.add_argument("--expected-endpoint-host", required=True)
    parser.add_argument("--allowed-bucket", action="append", required=True)
    parser.add_argument("--record-path", type=Path)
    parser.add_argument("--confirm", action="store_true")
    return parser


def _execute(args: argparse.Namespace) -> dict[str, object]:
    started_at = datetime.now(UTC)
    started = time.perf_counter()
    settings = FoundationSettings()
    manifest = parse_snapshot_manifest(read_manifest(args.manifest_path))
    references = run_reference_load(settings)
    client = create_s3_client(
        settings.object_store,
        endpoint_url=settings.object_store.endpoint,
    )
    try:
        result = restore_snapshot(
            client=client,
            manifest=manifest,
            expected_manifest_sha256=args.expected_manifest_sha256,
            endpoint_host=endpoint_host_from_url(settings.object_store.endpoint),
            expected_endpoint_host=args.expected_endpoint_host,
            allowed_buckets=frozenset(args.allowed_bucket),
            database_name=database_name_from_url(settings.database.url.get_secret_value()),
            expected_database_name=args.expected_database_name,
            restore_id=args.restore_id,
            references=references,
            confirm=args.confirm,
        )
    finally:
        client.close()
    record = result.to_record() | {
        "completed_at": datetime.now(UTC).isoformat(),
        "confirmed": bool(args.confirm),
        "duration_seconds": time.perf_counter() - started,
        "started_at": started_at.isoformat(),
    }
    if args.record_path is not None:
        write_private_record(args.record_path, record)
    return record


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ensure_asyncio_compatibility()
    try:
        record = _execute(args)
    except Exception as error:
        record = {
            "error_class": type(error).__name__,
            "operation": "r2-object-restore",
            "status": "failed",
        }
        print(json.dumps(record, separators=(",", ":"), sort_keys=True))
        return 1
    print(json.dumps(record, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
