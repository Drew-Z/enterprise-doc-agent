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
    result = subprocess.run(
        command, check=True, capture_output=True, text=True, encoding="utf-8", timeout=60
    )
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
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
    path.chmod(0o600)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_output_directory(root: Path, output_dir: Path) -> Path:
    resolved = output_dir.resolve()
    if resolved.is_relative_to(root.resolve()):
        raise LocalRecoveryDrillError("backup output directory must be outside the repository")
    if resolved.exists():
        raise LocalRecoveryDrillError("output directory already exists; choose a new directory")
    return resolved


def _artifact(path: Path, kind: str) -> dict[str, str]:
    return {"path": path.resolve().as_posix(), "kind": kind, "sha256": _sha256(path)}


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
    output_dir = validate_output_directory(root, output_dir)
    existing_target = _run_text(
        _psql_command(
            compose_file,
            user=postgres_user,
            database="postgres",
            sql=f"SELECT 1 FROM pg_database WHERE datname = '{restore_database}'",
        )
    )
    if existing_target:
        raise LocalRecoveryDrillError("restore database already exists; choose a new name")
    started_at = datetime.now(UTC)
    command_log = output_dir / "commands.log"
    backup = output_dir / "database.dump"
    before_path = output_dir / "inventory-before.json"
    after_path = output_dir / "inventory-after.json"
    output_dir.mkdir(parents=True, mode=0o700)
    commands: list[str] = []

    def record(command: list[str]) -> None:
        commands.append(subprocess.list2cmdline(command))
        command_log.write_text("\n".join(commands) + "\n", encoding="utf-8")
        command_log.chmod(0o600)

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
    with backup.open("xb") as stream:
        backup.chmod(0o600)
        subprocess.run(backup_command, check=True, stdout=stream, timeout=300)
    if backup.stat().st_size <= 0:
        raise LocalRecoveryDrillError("pg_dump produced an empty backup")
    backup_completed = time.time()

    create_command = compose_command(
        compose_file,
        "exec",
        "-T",
        "postgres",
        "createdb",
        "-U",
        postgres_user,
        restore_database,
    )
    record(create_command)
    # Successful exclusive creation establishes ownership; never clean a name we
    # only inspected, or one whose creation failed/has an uncertain outcome.
    subprocess.run(create_command, check=True, timeout=60)
    try:
        backup_age_at_restore = max(0.0, time.time() - backup_completed)
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
        with backup.open("rb") as restore_stream:
            subprocess.run(restore_command, check=True, stdin=restore_stream, timeout=300)
        restore_duration = time.monotonic() - restore_started
        after = _inventory(compose_file, user=postgres_user, database=restore_database)
        _write_json(after_path, after)
        comparable_after = {**after, "database": source_database}
        data_matches = before == comparable_after
        if not data_matches:
            raise LocalRecoveryDrillError("restored database inventory does not match source")
    finally:
        if not keep_restore_database:
            cleanup = compose_command(
                compose_file,
                "exec",
                "-T",
                "postgres",
                "dropdb",
                "-U",
                postgres_user,
                restore_database,
            )
            record(cleanup)
            subprocess.run(cleanup, check=True, timeout=60)

    completed_at = datetime.now(UTC)
    commit_sha = _run_text(["git", "-C", str(root.resolve()), "rev-parse", "HEAD"])
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
            "backup_age_seconds_at_restore": backup_age_at_restore,
            "restore_duration_seconds": restore_duration,
            "local_data_inventory_match": data_matches,
            "source_table_count": len(before["table_counts"]),
        },
        "smoke_checks": [
            {"name": "backup_integrity", "status": "passed"},
            {"name": "database_inventory", "status": "passed"},
            {"name": "data_integrity", "status": "not_executed"},
            {"name": "application_readiness", "status": "not_executed"},
            {"name": "rollback_readiness", "status": "not_executed"},
        ],
        "artifact_scope": "local-private",
        "artifacts": [
            _artifact(backup, "postgres-custom-backup"),
            _artifact(before_path, "source-inventory"),
            _artifact(after_path, "restore-inventory"),
            _artifact(command_log, "command-log"),
        ],
        "limitations": [
            "This local drill does not restore object-store versions.",
            "This local drill does not execute Kubernetes rollout rollback or authenticated smoke.",
            "Local timings are not production RPO or RTO measurements.",
            "Table counts and revisions do not prove row content or application correctness.",
            "Private absolute artifact paths must not be published as release evidence.",
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
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--source-database", default=os.environ.get("POSTGRES_DB", "enterprise_doc")
    )
    parser.add_argument("--restore-database", default="enterprise_doc_restore_drill")
    parser.add_argument(
        "--postgres-user", default=os.environ.get("POSTGRES_USER", "enterprise_doc")
    )
    parser.add_argument("--keep-restore-database", action="store_true")
    parser.add_argument("--confirm-local", action="store_true")
    parser.add_argument("--report-path", type=Path)
    args = parser.parse_args()
    try:
        validate_database_names(args.source_database, args.restore_database)
        output_dir = validate_output_directory(args.root, args.output_dir)
        report_path = args.report_path or output_dir / "report.json"
        if not report_path.resolve().is_relative_to(output_dir):
            raise LocalRecoveryDrillError("report path must be inside the private output directory")
        if not args.confirm_local:
            print(
                json.dumps(
                    {
                        "dry_run": True,
                        "source_database": args.source_database,
                        "restore_database": args.restore_database,
                        "compose_file": args.compose_file.as_posix(),
                        "output_dir": output_dir.as_posix(),
                        "report_path": report_path.as_posix(),
                    },
                    indent=2,
                )
            )
            return
        report = run_drill(
            root=args.root,
            compose_file=args.compose_file,
            output_dir=output_dir,
            source_database=args.source_database,
            restore_database=args.restore_database,
            postgres_user=args.postgres_user,
            keep_restore_database=args.keep_restore_database,
        )
        _write_json(report_path, report)
    except (OSError, LocalRecoveryDrillError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error)) from error
    print(json.dumps({"status": report["status"], "report": report_path.as_posix()}))


if __name__ == "__main__":
    main()
