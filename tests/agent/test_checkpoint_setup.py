from __future__ import annotations

from enterprise_doc_core.agents.checkpoint import (
    CheckpointerCommand,
    build_parser,
    normalize_postgres_dsn,
)


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
