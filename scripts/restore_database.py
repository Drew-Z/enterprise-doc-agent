from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

try:
    from scripts.backup_database import normalize_postgres_url
except ModuleNotFoundError:
    from backup_database import normalize_postgres_url


PRODUCTION_CONFIRMATION = "restore-production"
ALLOWED_ENVIRONMENTS = {"local", "test", "staging", "production"}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_restore_target(
    *,
    database_url: str,
    expected_host: str,
    environment: str,
    production_confirmation: str | None,
) -> str:
    if environment not in ALLOWED_ENVIRONMENTS:
        raise ValueError("restore environment is invalid")
    normalized = normalize_postgres_url(database_url)
    parsed = urlparse(normalized)
    if parsed.scheme not in {"postgresql", "postgres"} or parsed.hostname is None:
        raise ValueError("database URL must identify a PostgreSQL host")
    if parsed.hostname.lower() != expected_host.strip().lower():
        raise ValueError("database URL host does not match --expected-host")
    if environment == "production" and production_confirmation != PRODUCTION_CONFIRMATION:
        raise ValueError(
            f"production restore requires --confirm-production {PRODUCTION_CONFIRMATION}"
        )
    return normalized


def restore_command(*, database_url: str, backup: Path) -> list[str]:
    return [
        "pg_restore",
        "--exit-on-error",
        "--single-transaction",
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-privileges",
        "--dbname",
        normalize_postgres_url(database_url),
        str(backup),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Restore a PostgreSQL backup; dry-run unless --confirm is provided"
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--database-url-env", default="DATABASE__URL")
    parser.add_argument("--environment", choices=sorted(ALLOWED_ENVIRONMENTS), required=True)
    parser.add_argument("--expected-host", required=True)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--confirm-production")
    parser.add_argument("--record-path", type=Path)
    args = parser.parse_args()
    if not args.input.is_file():
        raise SystemExit("backup input does not exist")
    if args.input.stat().st_size <= 0:
        raise SystemExit("backup input is empty")
    database_url = os.environ.get(args.database_url_env)
    if not database_url:
        raise SystemExit(f"set {args.database_url_env}")
    backup_sha256 = file_sha256(args.input)
    if args.expected_sha256 is not None and args.expected_sha256.lower() != backup_sha256:
        raise SystemExit("backup SHA-256 does not match --expected-sha256")
    try:
        normalized_url = validate_restore_target(
            database_url=database_url,
            expected_host=args.expected_host,
            environment=args.environment,
            production_confirmation=args.confirm_production,
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error
    command = restore_command(database_url=normalized_url, backup=args.input)
    if not args.confirm:
        print("Dry run: restore command validated. Re-run with --confirm to execute.")
        return
    if args.expected_sha256 is None:
        raise SystemExit("confirmed restore requires --expected-sha256")
    if shutil.which("pg_restore") is None:
        raise SystemExit("pg_restore is required")
    started_at = datetime.now(UTC).isoformat()
    started = time.monotonic()
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as error:
        raise SystemExit("database restore failed") from error
    record = {
        "schema_version": 1,
        "operation": "postgres-restore",
        "status": "passed",
        "environment": args.environment,
        "target_host": args.expected_host.lower(),
        "started_at": started_at,
        "completed_at": datetime.now(UTC).isoformat(),
        "input_sha256": backup_sha256,
        "backup_age_seconds_at_restore": max(0.0, time.time() - args.input.stat().st_mtime),
        "restore_duration_seconds": max(0.0, time.monotonic() - started),
        "limitations": [
            "This record reports command completion; application smoke and measured RTO "
            "are separate gates."
        ],
    }
    rendered = json.dumps(record, indent=2, sort_keys=True) + "\n"
    if args.record_path is not None:
        args.record_path.parent.mkdir(parents=True, exist_ok=True)
        args.record_path.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
