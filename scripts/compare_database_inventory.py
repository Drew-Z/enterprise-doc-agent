from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

try:
    from scripts.backup_database import postgres_process_environment
    from scripts.restore_database import database_name_from_url, inspect_database_identity
except ModuleNotFoundError:
    from backup_database import (  # type: ignore[import-not-found,no-redef]
        postgres_process_environment,
    )
    from restore_database import (  # type: ignore[import-not-found,no-redef]
        database_name_from_url,
        inspect_database_identity,
    )


POSTGRES_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
MAX_AUDITED_TABLES = 40


def _run_query(database_url: str, sql: str) -> str:
    completed = subprocess.run(
        [
            "psql",
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--set=ON_ERROR_STOP=1",
            "--command",
            sql,
        ],
        check=True,
        capture_output=True,
        text=True,
        env=postgres_process_environment(database_url),
    )
    return completed.stdout.strip()


def database_inventory(database_url: str, schema: str) -> dict[str, object]:
    if POSTGRES_IDENTIFIER.fullmatch(schema) is None:
        raise ValueError("inventory schema must be a simple PostgreSQL identifier")
    table_sql = (
        "SELECT table_name FROM information_schema.tables "
        f"WHERE table_schema = '{schema}' AND table_type = 'BASE TABLE' "
        "ORDER BY table_name"
    )
    tables = [line for line in _run_query(database_url, table_sql).splitlines() if line]
    if any(POSTGRES_IDENTIFIER.fullmatch(table) is None for table in tables):
        raise RuntimeError("database returned a table name that cannot be audited safely")
    if not tables:
        raise RuntimeError("inventory schema contains no base tables")
    if len(tables) > MAX_AUDITED_TABLES:
        raise RuntimeError(f"inventory exceeds the audited limit of {MAX_AUDITED_TABLES} tables")
    if "alembic_version" not in tables:
        raise RuntimeError("inventory schema is missing alembic_version")

    count_pairs = ", ".join(
        f'\'{table}\', (SELECT count(*) FROM "{schema}"."{table}")' for table in tables
    )
    inventory_sql = (
        "SELECT json_build_object("
        f"'schema', '{schema}', "
        "'alembic_revisions', ("
        f"SELECT coalesce(json_agg(version_num ORDER BY version_num), '[]'::json) "
        f'FROM "{schema}"."alembic_version"'
        "), "
        f"'table_counts', json_build_object({count_pairs})"
        ")::text"
    )
    try:
        inventory = json.loads(_run_query(database_url, inventory_sql))
    except json.JSONDecodeError as error:
        raise RuntimeError("database inventory query did not return JSON") from error
    if not isinstance(inventory, dict) or not isinstance(inventory.get("table_counts"), dict):
        raise RuntimeError("database inventory query returned an invalid payload")
    if set(inventory["table_counts"]) != set(tables):
        raise RuntimeError("database inventory table set changed during inspection")
    return inventory


def compare_database_inventories(
    *,
    source_url: str,
    target_url: str,
    expected_source_database: str,
    expected_target_database: str,
    schema: str,
) -> dict[str, object]:
    if database_name_from_url(source_url) != expected_source_database:
        raise ValueError("source URL database does not match --expected-source-database")
    if database_name_from_url(target_url) != expected_target_database:
        raise ValueError("target URL database does not match --expected-target-database")
    source_identity = inspect_database_identity(source_url)
    target_identity = inspect_database_identity(target_url)
    if source_identity.get("current_database") != expected_source_database:
        raise ValueError("connected source database identity does not match")
    if target_identity.get("current_database") != expected_target_database:
        raise ValueError("connected target database identity does not match")
    source = database_inventory(source_url, schema)
    target = database_inventory(target_url, schema)
    target_counts = target.get("table_counts")
    if not isinstance(target_counts, dict):
        raise RuntimeError("target database inventory is missing table counts")
    matches = source == target
    return {
        "schema_version": 1,
        "operation": "postgres-inventory-compare",
        "status": "passed" if matches else "failed",
        "completed_at": datetime.now(UTC).isoformat(),
        "schema": schema,
        "source_database": expected_source_database,
        "target_database": expected_target_database,
        "source_server_address": source_identity.get("server_addr"),
        "target_server_address": target_identity.get("server_addr"),
        "alembic_revisions": target["alembic_revisions"],
        "table_count": len(target_counts),
        "table_counts": target_counts,
        "inventory_matches": matches,
        "values_redacted": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare bounded PostgreSQL schema inventories without exposing credentials"
    )
    parser.add_argument("--source-url-env", default="SOURCE_DATABASE_URL")
    parser.add_argument("--target-url-env", default="TARGET_DATABASE_URL")
    parser.add_argument("--expected-source-database", required=True)
    parser.add_argument("--expected-target-database", required=True)
    parser.add_argument("--schema", default="public")
    parser.add_argument("--record-path", type=Path, required=True)
    args = parser.parse_args()
    source_url = os.environ.get(args.source_url_env)
    target_url = os.environ.get(args.target_url_env)
    if not source_url:
        raise SystemExit(f"set {args.source_url_env}")
    if not target_url:
        raise SystemExit(f"set {args.target_url_env}")
    if shutil.which("psql") is None:
        raise SystemExit("psql is required")
    try:
        record = compare_database_inventories(
            source_url=source_url,
            target_url=target_url,
            expected_source_database=args.expected_source_database,
            expected_target_database=args.expected_target_database,
            schema=args.schema,
        )
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from error
    args.record_path.parent.mkdir(parents=True, exist_ok=True)
    args.record_path.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    args.record_path.chmod(0o600)
    print(json.dumps(record, indent=2, sort_keys=True))
    if record["status"] != "passed":
        raise SystemExit("database inventories do not match")


if __name__ == "__main__":
    main()
