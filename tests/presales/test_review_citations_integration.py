from __future__ import annotations

import asyncio
import csv
import importlib.util
import io
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, select

from enterprise_doc_core.billing.models import UsageReservation
from enterprise_doc_core.db.metadata import metadata
from enterprise_doc_core.documents.models import DocumentChunk, DocumentIngestionGeneration
from enterprise_doc_core.identity import Membership
from enterprise_doc_core.presales.models import PresalesAttempt, PresalesReview
from tests.browser_sessions.conftest import browser_db as browser_db
from tests.presales.legacy_review_schema import SavedReview as LegacySavedReview
from tests.presales.test_presales_review_changes_integration import (
    correction,
)
from tests.presales.test_presales_review_changes_integration import (
    review_case as review_case,
)
from tests.presales.test_presales_workflow_integration import workspace as workspace

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
async def citation_case(review_case):
    service, sessions, context, packet, client, calls = review_case
    chunk_id = uuid4()
    async with sessions.begin() as session:
        original = await session.get(DocumentChunk, packet.rows[0].draft.citations[0].chunk_id)
        values = {
            c.key: getattr(original, c.key)
            for c in DocumentChunk.__table__.columns
            if c.key not in {"id", "created_at", "updated_at"}
        }
        values.update(
            chunk_index=1,
            heading="Certificate requirements",
            normalized_text="A certificate or signed commitment is required.",
            end_offset=47,
            content_sha256="a" * 64,
        )
        session.add(DocumentChunk(id=chunk_id, **values))
    body = correction()
    body["citations"] = [
        {
            "chunkId": str(chunk_id),
            "documentVersionId": str(original.document_version_id),
            "excerpt": "A certificate or signed commitment is required.",
        }
    ]
    for item in body["prerequisites"]:
        item["citationIndexes"] = [0]
    yield service, sessions, context, packet, client, calls, body


async def test_corrected_evidence_survives_api_history_and_csv_without_changing_draft(
    citation_case, monkeypatch
):
    service, sessions, context, packet, client, calls, body = citation_case
    row_id = packet.rows[0].id
    path = f"/api/presales/{packet.id}/rows/{row_id}/review"
    headers = {"Authorization": "Bearer owner", "Idempotency-Key": "correct-citations"}

    async def no_embedding(*args, **kwargs):
        raise AssertionError("Review must not invoke embeddings")

    monkeypatch.setattr(service.generation.retriever.embedding_provider, "embed", no_embedding)
    async with sessions() as session:
        attempts_before = await session.scalar(select(func.count()).select_from(PresalesAttempt))
        reservations_before = [
            (r.id, r.state) for r in await session.scalars(select(UsageReservation))
        ]
    for _ in range(2):
        result = await client.put(path, headers=headers, json=body)
        assert result.status_code == 200, result.text
    row = result.json()["rows"][0]
    assert row["revision"] == 2 and len(row["reviewHistory"]) == 1
    saved = row["review"]["citations"][0]
    assert saved["excerpt"] == body["citations"][0]["excerpt"]
    assert saved["heading"] == "Certificate requirements"
    reloaded = (await service.get(context.principal, packet.id)).rows[0]
    assert reloaded.draft == packet.rows[0].draft
    assert reloaded.attempts == packet.rows[0].attempts and len(calls) == 1
    async with sessions() as session:
        stored = await session.scalar(select(PresalesReview).where(PresalesReview.row_id == row_id))
        assert stored.citations[0]["excerpt"] == saved["excerpt"]
        assert "citations" not in stored.content
        LegacySavedReview.model_validate(stored.content)
    exported = next(
        csv.DictReader(
            io.StringIO(
                (await service.export(context.principal, packet.id, "reviewed")).decode("utf-8-sig")
            )
        )
    )
    assert saved["excerpt"] in exported["原文证据"]
    assert saved["excerpt"] in exported["前提状态与对应证据"]
    assert saved["excerpt"] not in exported["原始引用证据"]
    assert "版本 2" in exported["人工引用修订记录"]
    next_body = {
        **body,
        "expectedRevision": 2,
        "citations": [
            {
                **body["citations"][0],
                "excerpt": "signed commitment",
            }
        ],
    }
    next_result = await client.put(
        path, headers={**headers, "Idempotency-Key": "next"}, json=next_body
    )
    assert next_result.status_code == 200, next_result.text
    history = next_result.json()["rows"][0]["reviewHistory"]
    assert [r["citations"][0]["excerpt"] for r in history] == [
        saved["excerpt"],
        "signed commitment",
    ]
    replay = await client.put(path, headers=headers, json=body)
    assert replay.status_code == 200 and replay.json()["rows"][0]["revision"] == 3
    async with sessions() as session:
        assert (
            await session.scalar(select(func.count()).select_from(PresalesAttempt))
            == attempts_before
        )
        assert [
            (r.id, r.state) for r in await session.scalars(select(UsageReservation))
        ] == reservations_before


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("fabricated", "presales_review_evidence_required"),
        ("wildcard", "presales_review_evidence_required"),
        ("foreign_chunk", "presales_review_evidence_required"),
        ("foreign_version", "presales_review_evidence_required"),
        ("metadata", "request_validation_failed"),
        ("duplicate", "request_validation_failed"),
        ("index", "presales_review_evidence_required"),
        ("empty", "presales_review_evidence_required"),
        ("note", "presales_review_note_required"),
    ],
)
async def test_invalid_citation_correction_is_atomic(citation_case, mutation, code):
    service, _sessions, context, packet, client, calls, body = citation_case
    citation = body["citations"][0]
    if mutation == "fabricated":
        citation["excerpt"] = "This is not a source quotation."
    elif mutation == "wildcard":
        citation["excerpt"] = "%"
    elif mutation == "foreign_chunk":
        citation["chunkId"] = str(uuid4())
    elif mutation == "foreign_version":
        citation["documentVersionId"] = str(uuid4())
    elif mutation == "metadata":
        citation["filename"] = "fabricated.pdf"
    elif mutation == "duplicate":
        body["citations"].append(dict(citation))
    elif mutation == "index":
        body["prerequisites"][0]["citationIndexes"] = [1]
    elif mutation == "empty":
        body["citations"] = []
    else:
        body["note"] = " "
    result = await client.put(
        f"/api/presales/{packet.id}/rows/{packet.rows[0].id}/review",
        headers={"Authorization": "Bearer owner", "Idempotency-Key": mutation},
        json=body,
    )
    assert result.status_code in {409, 422}, result.text
    assert result.json()["error"]["code"] == code
    assert (await service.get(context.principal, packet.id)).rows[0] == packet.rows[0]
    assert len(calls) == 1


async def test_citation_migration_preserves_legacy_and_refuses_history_loss(citation_case):
    service, sessions, context, packet, client, _calls, body = citation_case
    path = f"/api/presales/{packet.id}/rows/{packet.rows[0].id}/review"
    headers = {"Authorization": "Bearer owner", "Idempotency-Key": "legacy"}
    legacy = correction()
    response = await client.put(path, headers=headers, json=legacy)
    assert response.status_code == 200, response.text
    async with sessions() as session:
        old = await session.scalar(
            select(PresalesReview).where(PresalesReview.row_id == packet.rows[0].id)
        )
        old_content, old_fingerprint = old.content, old.fingerprint
        assert old.citations is None
    migration = (
        ROOT
        / "packages/core/src/enterprise_doc_core/db/migrations/versions"
        / "20261010_0037_presales_review_citations.py"
    )
    spec = importlib.util.spec_from_file_location("review_citations_migration", migration)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def migrate(connection, direction):
        with Operations.context(
            MigrationContext.configure(connection, opts={"target_metadata": metadata})
        ):
            getattr(module, direction)()

    engine = sessions.kw["bind"]
    async with engine.begin() as connection:
        await connection.run_sync(lambda c: migrate(c, "downgrade"))
        await connection.run_sync(lambda c: migrate(c, "upgrade"))
    replay = await client.put(path, headers=headers, json=legacy)
    assert replay.status_code == 200, replay.text
    async with sessions() as session:
        old = await session.scalar(
            select(PresalesReview).where(PresalesReview.row_id == packet.rows[0].id)
        )
        assert (old.content, old.fingerprint, old.citations) == (old_content, old_fingerprint, None)
    body["expectedRevision"] = 2
    result = await client.put(path, headers={**headers, "Idempotency-Key": "corrected"}, json=body)
    assert result.status_code == 200, result.text
    with pytest.raises(RuntimeError, match="presales_review_citations_history_present"):
        async with engine.begin() as connection:
            await connection.run_sync(lambda c: migrate(c, "downgrade"))
    assert (await service.get(context.principal, packet.id)).rows[0].review.citations


async def test_corrected_selection_requires_explicit_next_intent_and_preserves_conflicts(
    citation_case,
):
    service, _sessions, context, packet, client, calls, body = citation_case
    path = f"/api/presales/{packet.id}/rows/{packet.rows[0].id}/review"
    headers = {"Authorization": "Bearer owner"}
    results = await asyncio.gather(
        *[
            client.put(path, headers={**headers, "Idempotency-Key": key}, json=body)
            for key in ["first", "second"]
        ]
    )
    assert sorted(r.status_code for r in results) == [200, 409]
    winner = "first" if results[0].status_code == 200 else "second"
    changed = {**body, "citations": [{**body["citations"][0], "excerpt": "signed commitment"}]}
    result = await client.put(path, headers={**headers, "Idempotency-Key": winner}, json=changed)
    assert result.json()["error"]["code"] == "presales_idempotency_conflict"
    legacy = {**body, "expectedRevision": 2}
    del legacy["citations"]
    result = await client.put(path, headers={**headers, "Idempotency-Key": "legacy"}, json=legacy)
    assert result.json()["error"]["code"] == "presales_review_evidence_required"
    assert (await service.get(context.principal, packet.id)).rows[0].revision == 2
    reverted = {
        **body,
        "expectedRevision": 2,
        "note": "Explicitly restore original evidence.",
        "citations": [
            c.model_dump(
                mode="json", by_alias=True, include={"chunk_id", "document_version_id", "excerpt"}
            )
            for c in packet.rows[0].draft.citations
        ],
    }
    result = await client.put(
        path, headers={**headers, "Idempotency-Key": "restore"}, json=reverted
    )
    assert result.status_code == 200, result.text
    assert len(result.json()["rows"][0]["reviewHistory"]) == 2
    assert len(calls) == 1


@pytest.mark.parametrize("revoke", ["membership", "source"])
async def test_corrected_evidence_replay_reauthorizes_before_returning_content(
    citation_case, revoke
):
    _service, sessions, context, packet, client, _calls, body = citation_case
    path = f"/api/presales/{packet.id}/rows/{packet.rows[0].id}/review"
    headers = {"Authorization": "Bearer owner", "Idempotency-Key": "saved"}
    denied = await client.put(path, headers={**headers, "Authorization": "Bearer other"}, json=body)
    assert denied.status_code == 404
    saved = await client.put(path, headers=headers, json=body)
    assert saved.status_code == 200, saved.text
    async with sessions.begin() as session:
        if revoke == "membership":
            (await session.get(Membership, context.membership_id)).is_active = False
        else:
            (await session.get(DocumentIngestionGeneration, context.generation_id)).active = False
    for result in [
        await client.put(path, headers=headers, json=body),
        await client.get(f"/api/presales/{packet.id}", headers=headers),
    ]:
        assert result.status_code == (403 if revoke == "membership" else 404)
        assert "signed commitment" not in result.text


async def test_explicit_empty_evidence_is_audited_without_falling_back_to_draft(citation_case):
    service, _sessions, context, packet, client, _calls, body = citation_case
    body.update(
        citations=[],
        prerequisites=[],
        prerequisiteChanges={"origins": [], "excludedIndexes": [0, 1]},
        status="insufficient_evidence",
        conditions=[],
        missingInformation=["Need relevant certificate."],
        note="Original passages do not support this requirement.",
    )
    result = await client.put(
        f"/api/presales/{packet.id}/rows/{packet.rows[0].id}/review",
        headers={"Authorization": "Bearer owner", "Idempotency-Key": "empty"},
        json=body,
    )
    assert result.status_code == 200, result.text
    row = (await service.get(context.principal, packet.id)).rows[0]
    assert row.review.citations == [] and row.draft.citations
    exported = next(
        csv.DictReader(
            io.StringIO(
                (await service.export(context.principal, packet.id, "reviewed")).decode("utf-8-sig")
            )
        )
    )
    assert exported["原文证据"] == "" and exported["原始引用证据"]
    assert "无引用证据" in exported["人工引用修订记录"]
