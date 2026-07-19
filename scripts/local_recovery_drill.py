from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DATABASE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
TABLE_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")
RESTORE_PREFIX = "enterprise_doc_restore_"


class LocalRecoveryDrillError(RuntimeError):
    """Raised when the isolated local restore drill cannot be completed safely."""


def validate_database_names(source: str, restore: str) -> None:
    for name in (source, restore):
        if DATABASE_NAME.fullmatch(name) is None:
            raise LocalRecoveryDrillError(f"invalid PostgreSQL database name: {name}")
    if source == restore:
        raise LocalRecoveryDrillError("restore database must differ from source database")
    if not restore.startswith(RESTORE_PREFIX):
        raise LocalRecoveryDrillError(f"restore database must start with {RESTORE_PREFIX}")


def compose_command(compose_file: Path, *args: str) -> list[str]:
    return ["docker", "compose", "-f", compose_file.as_posix(), *args]


def _run_text(command: list[str]) -> str:
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _psql_command(
    compose_file: Path,
    *,
    user: str,
    database: str,
    sql: str,
) -> list[str]:
    return compose_command(
        compose_file,
        "exec",
        "-T",
        "postgres",
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        user,
        "-d",
        database,
        "-Atc",
        sql,
    )


def _inventory(compose_file: Path, *, user: str, database: str) -> dict[str, Any]:
    table_output = _run_text(
        _psql_command(
            compose_file,
            user=user,
            database=database,
            sql=(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name"
            ),
        )
    )
    tables = [line.strip() for line in table_output.splitlines() if line.strip()]
    if any(TABLE_NAME.fullmatch(table) is None for table in tables):
        raise LocalRecoveryDrillError(
            "database contains a table name that cannot be audited safely"
        )
    counts: dict[str, int] = {}
    for table in tables:
        value = _run_text(
            _psql_command(
                compose_file,
                user=user,
                database=database,
                sql=f'SELECT count(*) FROM "{table}"',
            )
        )
        try:
            counts[table] = int(value)
        except ValueError as error:
            raise LocalRecoveryDrillError(f"invalid row count for table {table}") from error
    revisions = _run_text(
        _psql_command(
            compose_file,
            user=user,
            database=database,
            sql="SELECT version_num FROM alembic_version ORDER BY version_num",
        )
    ).splitlines()
    return {"database": database, "alembic_revisions": revisions, "table_counts": counts}


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _repo_relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise LocalRecoveryDrillError("output directory must be inside the repository") from error


def _artifact(root: Path, path: Path, kind: str) -> dict[str, str]:
    return {"path": _repo_relative(root, path), "kind": kind, "sha256": _sha256(path)}


def run_drill(
    *,
    root: Path,
    compose_file: Path,
    output_dir: Path,
    source_database: str,
    restore_database: str,
    postgres_user: str,
    keep_restore_database: bool,
) -> dict[str, Any]:
    validate_database_names(source_database, restore_database)
    started_at = datetime.now(UTC)
    command_log = output_dir / "commands.log"
    backup = output_dir / "database.dump"
    before_path = output_dir / "inventory-before.json"
    after_path = output_dir / "inventory-after.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    commands: list[str] = []

    def record(command: list[str]) -> None:
        commands.append(subprocess.list2cmdline(command))
        command_log.write_text("\n".join(commands) + "\n", encoding="utf-8")

    before = _inventory(compose_file, user=postgres_user, database=source_database)
    _write_json(before_path, before)
    backup_command = compose_command(
        compose_file,
        "exec",
        "-T",
        "postgres",
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "-U",
        postgres_user,
        "-d",
        source_database,
    )
    record(backup_command)
    with backup.open("wb") as stream:
        subprocess.run(backup_command, check=True, stdout=stream)
    if backup.stat().st_size <= 0:
        raise LocalRecoveryDrillError("pg_dump produced an empty backup")
    backup_completed = time.time()

    admin = [
        compose_command(
            compose_file,
            "exec",
            "-T",
            "postgres",
            "dropdb",
            "--if-exists",
            "-U",
            postgres_user,
            restore_database,
        ),
        compose_command(
            compose_file,
            "exec",
            "-T",
            "postgres",
            "createdb",
            "-U",
            postgres_user,
            restore_database,
        ),
    ]
    for command in admin:
        record(command)
        subprocess.run(command, check=True)

    restore_started = time.monotonic()
    restore_command = compose_command(
        compose_file,
        "exec",
        "-T",
        "postgres",
        "pg_restore",
        "--exit-on-error",
        "--single-transaction",
        "--no-owner",
        "--no-privileges",
        "-U",
        postgres_user,
        "-d",
        restore_database,
    )
    record(restore_command)
    with backup.open("rb") as stream:
        subprocess.run(restore_command, check=True, stdin=stream)
    restore_duration = time.monotonic() - restore_started
    after = _inventory(compose_file, user=postgres_user, database=restore_database)
    _write_json(after_path, after)
    comparable_after = {**after, "database": source_database}
    data_matches = before == comparable_after
    if not data_matches:
        raise LocalRecoveryDrillError("restored database inventory does not match source")

    if not keep_restore_database:
        cleanup = admin[0]
        record(cleanup)
        subprocess.run(cleanup, check=True)

    completed_at = datetime.now(UTC)
    commit_sha = _run_text(["git", "rev-parse", "HEAD"])
    report = {
        "schema_version": 1,
        "evidence_type": "recovery",
        "evidence_id": f"local-recovery-{completed_at.strftime('%Y%m%dT%H%M%SZ')}",
        "milestone": "M6",
        "requirement_ids": ["M6-R6", "DR-5", "DR-9"],
        "status": "blocked_external",
        "environment": {
            "name": "local-compose-isolated-restore",
            "external_execution": False,
            "provider": None,
            "region": None,
            "cluster": None,
        },
        "blocking_reason": (
            "The local database restore passed, but no immutable Kubernetes release rollback "
            "or production-like RPO/RTO drill was executed."
        ),
        "prerequisites": [
            "Provide an isolated external restore target and versioned object-store backup.",
            "Provide immutable deployment digests and a compatible Kubernetes rollback revision.",
            "Run authenticated application smoke after restore and rollback.",
        ],
        "commit_sha": commit_sha,
        "image_digest": None,
        "operator": os.environ.get("USERNAME") or os.environ.get("USER") or "local-operator",
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "command_or_procedure": commands,
        "measurements": {
            "backup_age_seconds_at_restore": max(0.0, time.time() - backup_completed),
            "restore_duration_seconds": restore_duration,
            "local_data_inventory_match": data_matches,
            "source_table_count": len(before["table_counts"]),
        },
        "smoke_checks": [
            {"name": "backup_integrity", "status": "passed"},
            {"name": "data_integrity", "status": "passed"},
            {"name": "application_readiness", "status": "not_executed"},
            {"name": "rollback_readiness", "status": "not_executed"},
        ],
        "artifacts": [
            _artifact(root, backup, "postgres-custom-backup"),
            _artifact(root, before_path, "source-inventory"),
            _artifact(root, after_path, "restore-inventory"),
            _artifact(root, command_log, "command-log"),
        ],
        "limitations": [
            "This local drill does not restore object-store versions.",
            "This local drill does not execute Kubernetes rollout rollback or authenticated smoke.",
            "Local timings are not production RPO or RTO measurements.",
        ],
        "owner": "platform-engineering",
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run an isolated Docker Compose PostgreSQL backup and restore drill"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--compose-file", type=Path, default=Path("infra/compose/docker-compose.yml")
    )
    parser.add_argument("--output-dir", type=Path, default=Path("tmp/local-recovery-drill"))
    parser.add_argument(
        "--source-database", default=os.environ.get("POSTGRES_DB", "enterprise_doc")
    )
    parser.add_argument("--restore-database", default="enterprise_doc_restore_drill")
    parser.add_argument(
        "--postgres-user", default=os.environ.get("POSTGRES_USER", "enterprise_doc")
    )
    parser.add_argument("--keep-restore-database", action="store_true")
    parser.add_argument("--confirm-local", action="store_true")
    parser.add_argument(
        "--report-path", type=Path, default=Path("tmp/local-recovery-drill/report.json")
    )
    args = parser.parse_args()
    try:
        validate_database_names(args.source_database, args.restore_database)
        if not args.confirm_local:
            print(
                json.dumps(
                    {
                        "dry_run": True,
                        "source_database": args.source_database,
                        "restore_database": args.restore_database,
                        "compose_file": args.compose_file.as_posix(),
                    },
                    indent=2,
                )
            )
            return
        report = run_drill(
            root=args.root,
            compose_file=args.compose_file,
            output_dir=args.output_dir,
            source_database=args.source_database,
            restore_database=args.restore_database,
            postgres_user=args.postgres_user,
            keep_restore_database=args.keep_restore_database,
        )
        _write_json(args.report_path, report)
    except (OSError, LocalRecoveryDrillError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from error
    print(json.dumps({"status": report["status"], "report": args.report_path.as_posix()}))


if __name__ == "__main__":
    main()
