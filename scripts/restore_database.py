from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

try:
    from scripts.backup_database import (
        file_sha256,
        normalize_postgres_url,
        postgres_process_environment,
    )
except ModuleNotFoundError:
    from backup_database import (  # type: ignore[import-not-found,no-redef]
        file_sha256,
        normalize_postgres_url,
        postgres_process_environment,
    )


PRODUCTION_CONFIRMATION = "restore-production"
ALLOWED_ENVIRONMENTS = {"local", "test", "staging", "production"}
STAGING_RESTORE_PREFIX = "enterprise_doc_restore_"


def database_name_from_url(database_url: str) -> str:
    database = unquote(urlparse(normalize_postgres_url(database_url)).path.lstrip("/"))
    if not database:
        raise ValueError("database URL must identify a PostgreSQL database")
    return database


def postgres_client_major(executable: str) -> int:
    completed = subprocess.run(
        [executable, "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    match = re.search(r"PostgreSQL\)\s+(\d+)", completed.stdout)
    if match is None:
        raise RuntimeError(f"could not parse {executable} version")
    return int(match.group(1))


def inspect_archive(backup: Path) -> int:
    completed = subprocess.run(
        ["pg_restore", "--list", str(backup)],
        check=True,
        capture_output=True,
        text=True,
    )
    entries = [
        line
        for line in completed.stdout.splitlines()
        if line.strip() and not line.lstrip().startswith(";")
    ]
    if not entries:
        raise RuntimeError("PostgreSQL archive contains no restore entries")
    return len(entries)


def inspect_database_identity(database_url: str) -> dict[str, object]:
    query = (
        "SELECT json_build_object("
        "'current_database', current_database(), "
        "'current_user', current_user, "
        "'server_addr', inet_server_addr()::text, "
        "'server_port', inet_server_port(), "
        "'server_version_num', current_setting('server_version_num')::integer, "
        "'user_relation_count', ("
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname NOT IN ('pg_catalog', 'information_schema') "
        "AND n.nspname !~ '^pg_toast' AND c.relkind IN ('r', 'p', 'v', 'm', 'f')"
        "), "
        "'other_connection_count', ("
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE datname = current_database() AND pid <> pg_backend_pid()"
        ")"
        ")::text;"
    )
    completed = subprocess.run(
        [
            "psql",
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--set=ON_ERROR_STOP=1",
            "--command",
            query,
        ],
        check=True,
        capture_output=True,
        text=True,
        env=postgres_process_environment(database_url),
    )
    try:
        identity = json.loads(completed.stdout.strip())
    except json.JSONDecodeError as error:
        raise RuntimeError("database identity query did not return JSON") from error
    if not isinstance(identity, dict):
        raise RuntimeError("database identity query returned an invalid payload")
    return identity


def inspect_restore_preflight(
    *,
    database_url: str,
    backup: Path,
    expected_database: str,
    expected_server_address: str | None,
    require_empty: bool,
) -> dict[str, object]:
    archive_entry_count = inspect_archive(backup)
    client_major = postgres_client_major("pg_restore")
    identity = inspect_database_identity(database_url)
    if identity.get("current_database") != expected_database:
        raise ValueError("connected database does not match --expected-database")
    server_address = identity.get("server_addr")
    if expected_server_address is not None and server_address != expected_server_address:
        raise ValueError("connected server does not match --expected-server-address")
    server_version_num = identity.get("server_version_num")
    if not isinstance(server_version_num, int):
        raise RuntimeError("database identity is missing server_version_num")
    server_major = server_version_num // 10000
    if client_major < server_major:
        raise RuntimeError("pg_restore major version is older than the PostgreSQL server")
    relation_count = identity.get("user_relation_count")
    connection_count = identity.get("other_connection_count")
    if not isinstance(relation_count, int) or not isinstance(connection_count, int):
        raise RuntimeError("database identity is missing restore safety counters")
    if require_empty and relation_count != 0:
        raise ValueError("confirmed restore target must not contain user relations")
    if require_empty and connection_count != 0:
        raise ValueError("confirmed restore target has other active connections")
    return {
        "archive_entry_count": archive_entry_count,
        "pg_restore_client_major": client_major,
        **identity,
    }


def validate_restore_target(
    *,
    database_url: str,
    expected_host: str,
    expected_database: str,
    source_database: str | None,
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
    database = unquote(parsed.path.lstrip("/"))
    if not database:
        raise ValueError("database URL must identify a PostgreSQL database")
    if database != expected_database.strip():
        raise ValueError("database URL database does not match --expected-database")
    if environment == "staging":
        if not source_database:
            raise ValueError("staging restore requires --source-database")
        if database == source_database.strip():
            raise ValueError("staging restore database must differ from source database")
        if not database.startswith(STAGING_RESTORE_PREFIX):
            raise ValueError(f"staging restore database must start with {STAGING_RESTORE_PREFIX}")
    if environment == "production" and production_confirmation != PRODUCTION_CONFIRMATION:
        raise ValueError(
            f"production restore requires --confirm-production {PRODUCTION_CONFIRMATION}"
        )
    return normalized


def restore_command(*, database_url: str, backup: Path) -> list[str]:
    database = database_name_from_url(database_url)
    return [
        "pg_restore",
        "--dbname",
        database,
        "--exit-on-error",
        "--single-transaction",
        "--no-owner",
        "--no-privileges",
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
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--expected-server-address")
    parser.add_argument("--source-database")
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
            expected_database=args.expected_database,
            source_database=args.source_database,
            environment=args.environment,
            production_confirmation=args.confirm_production,
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error
    command = restore_command(database_url=normalized_url, backup=args.input)
    if shutil.which("pg_restore") is None:
        raise SystemExit("pg_restore is required")
    if shutil.which("psql") is None:
        raise SystemExit("psql is required")
    if args.confirm and args.expected_server_address is None:
        raise SystemExit("confirmed restore requires --expected-server-address")
    try:
        preflight = inspect_restore_preflight(
            database_url=normalized_url,
            backup=args.input,
            expected_database=args.expected_database,
            expected_server_address=args.expected_server_address,
            require_empty=args.confirm,
        )
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from error
    if not args.confirm:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "operation": "postgres-restore-preflight",
                    "status": "validated",
                    "target_host": args.expected_host.lower(),
                    "target_database": args.expected_database,
                    **preflight,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return
    if args.expected_sha256 is None:
        raise SystemExit("confirmed restore requires --expected-sha256")
    started_at = datetime.now(UTC).isoformat()
    started = time.monotonic()
    try:
        subprocess.run(
            command,
            check=True,
            env=postgres_process_environment(normalized_url),
        )
    except subprocess.CalledProcessError as error:
        raise SystemExit("database restore failed") from error
    try:
        restored_identity = inspect_database_identity(normalized_url)
    except (RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit("database restore completed but post-restore identity failed") from error
    if restored_identity.get("current_database") != args.expected_database:
        raise SystemExit("post-restore database identity does not match target")
    if restored_identity.get("server_addr") != args.expected_server_address:
        raise SystemExit("post-restore server identity does not match target")
    restored_relations = restored_identity.get("user_relation_count")
    if not isinstance(restored_relations, int) or restored_relations <= 0:
        raise SystemExit("database restore completed without restored user relations")
    record = {
        "schema_version": 1,
        "operation": "postgres-restore",
        "status": "passed",
        "environment": args.environment,
        "target_host": args.expected_host.lower(),
        "target_server_address": preflight["server_addr"],
        "target_server_port": preflight["server_port"],
        "target_user": preflight["current_user"],
        "source_database": args.source_database,
        "target_database": args.expected_database,
        "server_version_num": preflight["server_version_num"],
        "pg_restore_client_major": preflight["pg_restore_client_major"],
        "archive_entry_count": preflight["archive_entry_count"],
        "target_user_relation_count_before": preflight["user_relation_count"],
        "target_other_connection_count_before": preflight["other_connection_count"],
        "target_user_relation_count_after": restored_relations,
        "started_at": started_at,
        "completed_at": datetime.now(UTC).isoformat(),
        "input_sha256": backup_sha256,
        "backup_age_seconds_at_restore": max(0.0, time.time() - args.input.stat().st_mtime),
        "restore_duration_seconds": max(0.0, time.monotonic() - started),
        "limitations": [
            "This record reports command completion; application smoke and measured RTO "
            "are separate gates.",
            "Object-store versions are not restored or validated by this database command.",
        ],
    }
    rendered = json.dumps(record, indent=2, sort_keys=True) + "\n"
    if args.record_path is not None:
        args.record_path.parent.mkdir(parents=True, exist_ok=True)
        args.record_path.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
