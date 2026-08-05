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
    run_reference_load,
    write_private_bytes,
    write_private_record,
)
from enterprise_doc_core.recovery.object_store import create_snapshot, endpoint_host_from_url


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a verified application-level immutable object-store snapshot"
    )
    parser.add_argument("--drill-id", required=True)
    parser.add_argument("--expected-endpoint-host", required=True)
    parser.add_argument("--allowed-bucket", action="append", required=True)
    parser.add_argument("--manifest-bucket")
    parser.add_argument("--manifest-path", type=Path, required=True)
    parser.add_argument("--record-path", type=Path)
    parser.add_argument("--confirm", action="store_true")
    return parser


def _execute(args: argparse.Namespace) -> dict[str, object]:
    started_at = datetime.now(UTC)
    started = time.perf_counter()
    settings = FoundationSettings()
    references = run_reference_load(settings)
    client = create_s3_client(
        settings.object_store,
        endpoint_url=settings.object_store.endpoint,
    )
    try:
        result = create_snapshot(
            client=client,
            endpoint_host=endpoint_host_from_url(settings.object_store.endpoint),
            expected_endpoint_host=args.expected_endpoint_host,
            allowed_buckets=frozenset(args.allowed_bucket),
            manifest_bucket=args.manifest_bucket or settings.object_store.documents_bucket,
            drill_id=args.drill_id,
            references=references,
            confirm=args.confirm,
        )
    finally:
        client.close()
    if args.confirm:
        write_private_bytes(args.manifest_path, result.manifest.render(), immutable=True)
    record = result.to_record() | {
        "completed_at": datetime.now(UTC).isoformat(),
        "confirmed": bool(args.confirm),
        "duration_seconds": time.perf_counter() - started,
        "manifest_output": args.manifest_path.name if args.confirm else None,
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
            "operation": "r2-object-snapshot",
            "status": "failed",
        }
        print(json.dumps(record, separators=(",", ":"), sort_keys=True))
        return 1
    print(json.dumps(record, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
