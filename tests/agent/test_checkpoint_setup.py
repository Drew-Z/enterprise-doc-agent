from __future__ import annotations

from enterprise_doc_core.agents.checkpoint import (
    CheckpointerCommand,
    build_parser,
    normalize_postgres_dsn,
)
from enterprise_doc_core.db.metadata import LANGGRAPH_CHECKPOINT_TABLES, include_alembic_name


def test_checkpointer_parser_supports_read_only_check_mode() -> None:
    args = build_parser().parse_args(["--check"])

    assert args.command is CheckpointerCommand.CHECK


def test_checkpointer_parser_defaults_to_setup_mode() -> None:
    args = build_parser().parse_args([])

    assert args.command is CheckpointerCommand.SETUP


def test_sqlalchemy_psycopg_url_is_normalized_for_official_saver() -> None:
    assert normalize_postgres_dsn("postgresql+psycopg://user:secret@db/app") == (
        "postgresql://user:secret@db/app"
    )


def test_alembic_does_not_claim_official_checkpointer_tables() -> None:
    assert LANGGRAPH_CHECKPOINT_TABLES == {
        "checkpoint_migrations",
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
    }
    for table_name in LANGGRAPH_CHECKPOINT_TABLES:
        assert include_alembic_name(table_name, "table", {}) is False
    assert include_alembic_name("agent_runs", "table", {}) is True
