from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from enterprise_doc_core.config import FoundationSettings
from enterprise_doc_core.documents import reindex


class _FakeEngine:
    async def dispose(self) -> None:
        pass


class _FakeSession:
    def __init__(self, versions: list[SimpleNamespace]) -> None:
        self.versions = versions

    async def scalars(self, _: object) -> SimpleNamespace:
        return SimpleNamespace(all=lambda: self.versions)


class _FakeBegin(AbstractAsyncContextManager[_FakeSession]):
    def __init__(self, session: _FakeSession) -> None:
        self.session = session

    async def __aenter__(self) -> _FakeSession:
        return self.session

    async def __aexit__(self, *args: object) -> None:
        return None


class _FakeSessionFactory:
    def __init__(self, versions: list[SimpleNamespace]) -> None:
        self.session = _FakeSession(versions)

    def begin(self) -> _FakeBegin:
        return _FakeBegin(self.session)


@pytest.mark.asyncio
async def test_reindex_jobs_use_configured_ingestion_retry_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version = SimpleNamespace(tenant_id=uuid4(), created_by=uuid4(), id=uuid4())
    engine = _FakeEngine()
    session_factory = _FakeSessionFactory([version])
    captured: list[dict[str, Any]] = []

    async def fake_create_job_records(_: object, **kwargs: Any) -> SimpleNamespace:
        captured.append(kwargs)
        return SimpleNamespace(replayed=False)

    monkeypatch.setattr(reindex, "create_database_engine", lambda _: engine)
    monkeypatch.setattr(reindex, "create_session_factory", lambda _: session_factory)
    monkeypatch.setattr(reindex, "create_job_records", fake_create_job_records)
    settings = FoundationSettings(
        _env_file=None,
        embedding={"ingestion_max_attempts": 5},
    )

    report = await reindex.enqueue_reindex(
        settings,
        apply=True,
        tenant_id=None,
        limit=10,
    )

    assert report["created"] == 1
    assert captured[0]["max_attempts"] == 5
