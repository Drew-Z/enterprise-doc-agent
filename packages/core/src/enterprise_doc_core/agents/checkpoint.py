from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from enterprise_doc_core.config import FoundationSettings
from enterprise_doc_core.db import ensure_asyncio_compatibility


class CheckpointerCommand(StrEnum):
    SETUP = "setup"
    CHECK = "check"


@dataclass(frozen=True, slots=True)
class CheckpointerReadiness:
    command: CheckpointerCommand
    ready: bool
    migration_version: int | None
    expected_migration_version: int
    missing_tables: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


REQUIRED_CHECKPOINT_TABLES = (
    "checkpoint_migrations",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="enterprise-doc-checkpointer-setup",
        description="Set up or check the official LangGraph PostgreSQL checkpoint schema.",
    )
    parser.add_argument(
        "--check",
        action="store_const",
        const=CheckpointerCommand.CHECK,
        default=CheckpointerCommand.SETUP,
        dest="command",
        help="Run read-only readiness checks without changing the database.",
    )
    return parser


def normalize_postgres_dsn(url: str) -> str:
    prefix = "postgresql+psycopg://"
    if url.startswith(prefix):
        return f"postgresql://{url.removeprefix(prefix)}"
    return url


def _checkpoint_dsn(settings: FoundationSettings) -> str:
    configured = settings.agent.checkpoint_url
    source = configured or settings.database.url
    return normalize_postgres_dsn(source.get_secret_value())


def _strict_serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(
        pickle_fallback=False,
        allowed_json_modules=None,
        allowed_msgpack_modules=None,
    )


async def setup_checkpoint_schema(settings: FoundationSettings) -> CheckpointerReadiness:
    async with asyncio.timeout(settings.agent.checkpoint_timeout_seconds):
        async with AsyncPostgresSaver.from_conn_string(
            _checkpoint_dsn(settings),
            serde=_strict_serializer(),
        ) as saver:
            await saver.setup()
    readiness = await check_checkpoint_schema(settings)
    return CheckpointerReadiness(
        command=CheckpointerCommand.SETUP,
        ready=readiness.ready,
        migration_version=readiness.migration_version,
        expected_migration_version=readiness.expected_migration_version,
        missing_tables=readiness.missing_tables,
    )


async def check_checkpoint_schema(settings: FoundationSettings) -> CheckpointerReadiness:
    expected_version = len(AsyncPostgresSaver.MIGRATIONS) - 1
    missing_tables: tuple[str, ...]
    migration_version: int | None = None
    async with asyncio.timeout(settings.agent.checkpoint_timeout_seconds):
        async with await AsyncConnection.connect(
            _checkpoint_dsn(settings),
            autocommit=False,
            prepare_threshold=0,
            row_factory=dict_row,
        ) as connection:
            async with connection.transaction():
                await connection.execute("SET TRANSACTION READ ONLY")
                cursor = await connection.execute(
                    """
                    SELECT required.name, to_regclass(required.name) IS NOT NULL AS present
                    FROM unnest(%s::text[]) AS required(name)
                    ORDER BY required.name
                    """,
                    (list(REQUIRED_CHECKPOINT_TABLES),),
                )
                rows = await cursor.fetchall()
                missing_tables = tuple(str(row["name"]) for row in rows if not row["present"])
                if not missing_tables:
                    version_cursor = await connection.execute(
                        "SELECT MAX(v) AS migration_version FROM checkpoint_migrations"
                    )
                    version_row = await version_cursor.fetchone()
                    if version_row is not None and version_row["migration_version"] is not None:
                        migration_version = int(version_row["migration_version"])

    return CheckpointerReadiness(
        command=CheckpointerCommand.CHECK,
        ready=not missing_tables and migration_version == expected_version,
        migration_version=migration_version,
        expected_migration_version=expected_version,
        missing_tables=missing_tables,
    )


async def _run(command: CheckpointerCommand) -> CheckpointerReadiness:
    settings = FoundationSettings()
    if command is CheckpointerCommand.CHECK:
        return await check_checkpoint_schema(settings)
    return await setup_checkpoint_schema(settings)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ensure_asyncio_compatibility()
    try:
        readiness = asyncio.run(_run(args.command))
    except Exception as error:
        print(
            json.dumps(
                {
                    "command": args.command.value,
                    "ready": False,
                    "error": type(error).__name__,
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(readiness.to_dict(), separators=(",", ":"), sort_keys=True))
    return 0 if readiness.ready else 1
