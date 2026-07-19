from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit

POSTGRES_CONNECTION_ENV_KEYS = frozenset(
    {
        "PGAPPNAME",
        "PGDATABASE",
        "PGHOST",
        "PGPASSFILE",
        "PGPASSWORD",
        "PGPORT",
        "PGSERVICE",
        "PGSSLCERT",
        "PGSSLKEY",
        "PGSSLROOTCERT",
        "PGSSLMODE",
        "PGUSER",
    }
)


def normalize_postgres_url(value: str) -> str:
    parsed = urlsplit(value)
    scheme = parsed.scheme.split("+", maxsplit=1)[0]
    if scheme not in {"postgres", "postgresql"}:
        raise ValueError("database URL must use PostgreSQL")
    return urlunsplit(("postgresql", parsed.netloc, parsed.path, parsed.query, parsed.fragment))


def postgres_process_environment(database_url: str) -> dict[str, str]:
    """Build a libpq environment without placing credentials in argv."""
    parsed = urlsplit(normalize_postgres_url(database_url))
    environment = os.environ.copy()
    for key in POSTGRES_CONNECTION_ENV_KEYS:
        environment.pop(key, None)
    if parsed.hostname:
        environment["PGHOST"] = parsed.hostname
    if parsed.port is not None:
        environment["PGPORT"] = str(parsed.port)
    if parsed.username:
        environment["PGUSER"] = unquote(parsed.username)
    if parsed.password:
        environment["PGPASSWORD"] = unquote(parsed.password)
    database = parsed.path.lstrip("/")
    if database:
        environment["PGDATABASE"] = unquote(database)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query_env = {
        "sslmode": "PGSSLMODE",
        "sslcert": "PGSSLCERT",
        "sslkey": "PGSSLKEY",
        "sslrootcert": "PGSSLROOTCERT",
        "application_name": "PGAPPNAME",
    }
    for query_name, env_name in query_env.items():
        values = query.get(query_name)
        if values and values[-1]:
            environment[env_name] = unquote(values[-1])
    return environment


def run_backup(*, database_url: str, output: Path, overwrite: bool) -> dict[str, object]:
    if shutil.which("pg_dump") is None:
        raise RuntimeError("pg_dump is required")
    if output.exists() and not overwrite:
        raise FileExistsError("backup output already exists; use --overwrite explicitly")
    output.parent.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(UTC).isoformat()
    subprocess.run(
        [
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--file",
            str(output),
        ],
        check=True,
        env=postgres_process_environment(database_url),
    )
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    return {
        "schema_version": 1,
        "operation": "postgres-backup",
        "status": "passed",
        "started_at": started_at,
        "completed_at": datetime.now(UTC).isoformat(),
        "output": output.name,
        "size_bytes": output.stat().st_size,
        "sha256": digest,
        "limitations": [
            "Backup creation alone does not prove restore success or an RPO/RTO objective."
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a PostgreSQL custom-format backup")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database-url-env", default="DATABASE__URL")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--record-path", type=Path)
    args = parser.parse_args()
    database_url = os.environ.get(args.database_url_env)
    if not database_url:
        raise SystemExit(f"set {args.database_url_env}")
    try:
        record = run_backup(
            database_url=database_url,
            output=args.output,
            overwrite=args.overwrite,
        )
    except (FileExistsError, OSError, RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from error
    rendered = json.dumps(record, indent=2, sort_keys=True) + "\n"
    if args.record_path is not None:
        args.record_path.parent.mkdir(parents=True, exist_ok=True)
        args.record_path.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
