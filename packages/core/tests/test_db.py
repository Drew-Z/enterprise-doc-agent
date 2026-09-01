from __future__ import annotations

import asyncio
import subprocess
import sys

import pytest

import enterprise_doc_core.db.engine as engine_module
from enterprise_doc_core.config import DatabaseSettings
from enterprise_doc_core.db import selector_event_loop_factory


def test_selector_event_loop_factory_is_psycopg_compatible_on_windows() -> None:
    loop = selector_event_loop_factory()
    try:
        assert isinstance(loop, asyncio.AbstractEventLoop)
        if sys.platform == "win32":
            assert type(loop).__name__ == "_WindowsSelectorEventLoop"
    finally:
        loop.close()


def test_production_session_factory_registers_all_foreign_key_targets() -> None:
    code = """
from sqlalchemy.orm import configure_mappers
from enterprise_doc_core.db import Base, create_session_factory
from enterprise_doc_worker.consumer_main import build_consumer_app

create_session_factory(None)
configure_mappers()
required = {
    "tenants",
    "users",
    "documents",
    "document_chunks",
    "outbox_events",
    "local_token_revocations",
}
missing = required.difference(Base.metadata.tables)
if missing:
    raise SystemExit(f"missing model tables: {sorted(missing)}")
assert build_consumer_app is not None
"""
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)


def test_database_engine_uses_explicit_bounded_pool_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    sentinel = object()

    def fake_create_async_engine(url: str, **kwargs: object) -> object:
        captured["url"] = url
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(engine_module, "create_async_engine", fake_create_async_engine)
    settings = DatabaseSettings(
        url="postgresql+psycopg://user:password@db.example/app",
        connect_timeout_seconds=12,
        pool_size=3,
        max_overflow=2,
        pool_timeout_seconds=8,
        pool_recycle_seconds=600,
        prepare_threshold=None,
    )

    assert engine_module.create_database_engine(settings) is sentinel
    assert captured == {
        "url": "postgresql+psycopg://user:password@db.example/app",
        "pool_pre_ping": True,
        "pool_size": 3,
        "max_overflow": 2,
        "pool_timeout": 8,
        "pool_recycle": 600,
        "connect_args": {"connect_timeout": 12.0, "prepare_threshold": None},
    }
