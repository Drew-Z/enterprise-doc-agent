from __future__ import annotations

import asyncio
import csv
import importlib.util
import io
import json
from pathlib import Path

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import select

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.config import ModelProvider, ModelSettings
from enterprise_doc_core.db.metadata import metadata
from enterprise_doc_core.identity import Membership
from enterprise_doc_core.presales.gateway import OpenAICompatiblePresalesGateway
from enterprise_doc_core.presales.models import PresalesReview, PresalesRow
from tests.browser_sessions.conftest import browser_db as browser_db
from tests.presales.legacy_review_schema import SavedReview as LegacySavedReview
from tests.presales.test_presales_workflow_integration import workspace as workspace

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
async def review_case(workspace):
    service, sessions, context, other, _, payload = workspace
    calls = []

    def respond(request):
        sent = json.loads(json.loads(request.content)["messages"][1]["content"])
        calls.append(sent)
        refs = [{"citationId": e["citationId"]} for e in sent["evidence"]]
        content = {
            "prerequisites": [
                {
                    "proposition": text,
                    "uncertainty": "missing",
                    "positive": [],
                    "negative": [],
                    "definition": [
                        {"citationId": e["citationId"], "text": e["text"]} for e in sent["evidence"]
                    ],
                    "unconfirmed": [],
                }
                for text in ["期限与验收均已确认。", "采购培训是服务前提。"]
            ],
            "status": "conditional",
            "answer": "需核查混合命题与多余前提。",
            "missingInformation": ["请确认验收情况。"],
            "citations": refs,
        }
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(content)},
                    }
                ]
            },
        )

    service.generation.gateway = OpenAICompatiblePresalesGateway(
        ModelSettings(
            provider=ModelProvider.OPENAI_COMPATIBLE,
            base_url="https://review.invalid/v1",
            api_key=SecretStr("test-only"),
            model_name="review-fixture",
        ),
        transport=httpx.MockTransport(respond),
    )
    packet = await service.create(context.principal, payload, "review-changes-create")
    packet = await service.generate(context.principal, packet.id, packet.rows[0].id, "generate")
    assert packet.rows[0].draft is not None

    class Resolver:
        async def resolve(self, token):
            return other.principal if token == "other" else context.principal

    app = create_app(
        settings=ApiSettings(_env_file=None),
        checkers=[],
        principal_resolver=Resolver(),
        presales_service=service,
    )
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            yield service, sessions, context, packet, client, calls


def correction():
    return {
        "expectedRevision": 1,
        "status": "conditional",
        "answer": "人工拆分期限与验收。排除无依据培训前提。",
        "conditions": ["验收已确认。"],
        "missingInformation": ["请确认验收并提供记录。"],
        "prerequisites": [
            {"condition": "期限已确认。", "state": "met", "citationIndexes": [0]},
            {"condition": "验收已确认。", "state": "unknown", "citationIndexes": [0]},
            {"condition": "第二份条款适用于本次采购。", "state": "met", "citationIndexes": [1]},
        ],
        "prerequisiteChanges": {"origins": [0, 0, None], "excludedIndexes": [1]},
        "note": "合成回归。拆分原项1、新增资料适用性、排除原项2。不代表真实业务批准。",
    }


async def test_human_split_add_rewrite_exclude_survive_api_history_and_csv(review_case):
    service, sessions, context, packet, client, calls = review_case
    row_id = packet.rows[0].id
    path = f"/api/presales/{packet.id}/rows/{row_id}/review"
    headers = {"Authorization": "Bearer owner", "Idempotency-Key": "correct-once"}
    original = packet.rows[0].draft.model_dump(mode="json")
    payload = correction()
    for _ in range(2):
        result = await client.put(path, headers=headers, json=payload)
        assert result.status_code == 200, result.text
    row = result.json()["rows"][0]
    assert row["revision"] == 2 and len(row["reviewHistory"]) == 1
    assert row["review"]["prerequisiteChanges"] == payload["prerequisiteChanges"]
    assert row["review"]["prerequisites"] == payload["prerequisites"]
    reloaded = await service.get(context.principal, packet.id)
    assert reloaded.rows[0].draft.model_dump(mode="json") == original
    assert len(calls) == 1
    async with sessions() as session:
        stored = await session.scalar(select(PresalesReview).where(PresalesReview.row_id == row_id))
        assert stored.prerequisite_changes == {"origins": [0, 0, None], "excluded_indexes": [1]}
        assert "prerequisite_changes" not in stored.content
        assert (await session.get(PresalesRow, row_id)).draft == original
    exported = next(
        csv.DictReader(
            io.StringIO(
                (await service.export(context.principal, packet.id, "reviewed")).decode("utf-8-sig")
            )
        )
    )
    assert "原项 1" in exported["人工前提修订记录"]
    assert "人工新增" in exported["人工前提修订记录"]
    assert "排除原项 2" in exported["人工前提修订记录"]
    assert "期限与验收均已确认" in exported["原模型前提状态与对应证据"]
    assert "期限已确认" in exported["前提状态与对应证据"]

    updated = correction()
    updated["expectedRevision"] = 2
    updated["prerequisites"][1]["condition"] = "验收记录已复核。"
    updated["conditions"] = ["验收记录已复核。"]
    next_response = await client.put(
        path, headers={**headers, "Idempotency-Key": "next-review"}, json=updated
    )
    assert next_response.status_code == 200, next_response.text
    history = next_response.json()["rows"][0]["reviewHistory"]
    assert len(history) == 2
    assert history[0]["prerequisites"] == payload["prerequisites"]
    assert history[1]["prerequisites"] == updated["prerequisites"]
    assert all(x["prerequisiteChanges"] == payload["prerequisiteChanges"] for x in history)


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("no_changes", "presales_review_prerequisites_invalid"),
        ("missing_original", "presales_review_prerequisites_invalid"),
        ("included_and_excluded", "presales_review_prerequisites_invalid"),
        ("foreign_original", "presales_review_prerequisites_invalid"),
        ("no_note", "presales_review_note_required"),
        ("foreign_evidence", "presales_review_evidence_required"),
        ("missing_origin", "request_validation_failed"),
        ("boolean_origin", "request_validation_failed"),
        ("duplicate_exclusion", "request_validation_failed"),
        ("null_prerequisites", "request_validation_failed"),
        ("fabricated_quote", "request_validation_failed"),
        ("empty_evidence", "request_validation_failed"),
    ],
)
async def test_invalid_human_revision_never_writes_history_or_changes_the_draft(
    review_case, mutation, code
):
    service, sessions, context, packet, client, calls = review_case
    body = correction()
    changes = body["prerequisiteChanges"]
    if mutation == "no_changes":
        del body["prerequisiteChanges"]
    elif mutation == "missing_original":
        changes["excludedIndexes"] = []
    elif mutation == "included_and_excluded":
        changes["excludedIndexes"] = [0, 1]
    elif mutation == "foreign_original":
        changes["origins"][1] = 2
    elif mutation == "no_note":
        body["note"] = " "
    elif mutation == "foreign_evidence":
        body["prerequisites"][0]["citationIndexes"] = [11]
    elif mutation == "missing_origin":
        changes["origins"].pop()
    elif mutation == "boolean_origin":
        changes["origins"][0] = True
    elif mutation == "duplicate_exclusion":
        changes["excludedIndexes"] = [1, 1]
    elif mutation == "null_prerequisites":
        body["prerequisites"] = None
    elif mutation == "fabricated_quote":
        body["prerequisites"][0]["excerpt"] = "并不存在的原文"
    else:
        body["prerequisites"][0]["citationIndexes"] = []
    result = await client.put(
        f"/api/presales/{packet.id}/rows/{packet.rows[0].id}/review",
        headers={"Authorization": "Bearer owner", "Idempotency-Key": mutation},
        json=body,
    )
    assert result.status_code == (409 if mutation == "foreign_evidence" else 422), result.text
    assert result.json()["error"]["code"] == code
    reloaded = await service.get(context.principal, packet.id)
    assert reloaded.rows[0] == packet.rows[0]
    assert len(calls) == 1
    async with sessions() as session:
        assert (
            await session.scalar(
                select(PresalesReview).where(PresalesReview.row_id == packet.rows[0].id)
            )
            is None
        )


async def test_human_revision_retains_conflicts_idempotency_and_revocation(review_case):
    _service, sessions, context, packet, client, _ = review_case
    path = f"/api/presales/{packet.id}/rows/{packet.rows[0].id}/review"
    body = correction()
    foreign = await client.put(
        path, headers={"Authorization": "Bearer other", "Idempotency-Key": "foreign"}, json=body
    )
    assert foreign.status_code == 404
    responses = await asyncio.gather(
        *[
            client.put(
                path, headers={"Authorization": "Bearer owner", "Idempotency-Key": key}, json=body
            )
            for key in ["concurrent-a", "concurrent-b"]
        ]
    )
    assert sorted(x.status_code for x in responses) == [200, 409]
    winner = "concurrent-a" if responses[0].status_code == 200 else "concurrent-b"
    changed = correction()
    changed["prerequisiteChanges"]["origins"][1] = None
    conflict = await client.put(
        path, headers={"Authorization": "Bearer owner", "Idempotency-Key": winner}, json=changed
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "presales_idempotency_conflict"
    async with sessions.begin() as session:
        membership = await session.get(Membership, context.membership_id)
        membership.is_active = False
    denied = await client.put(
        path, headers={"Authorization": "Bearer owner", "Idempotency-Key": winner}, json=body
    )
    assert denied.status_code == 403
    denied_export = await client.get(
        f"/api/presales/{packet.id}/export?mode=reviewed", headers={"Authorization": "Bearer owner"}
    )
    assert denied_export.status_code == 403


@pytest.mark.parametrize("legacy", [False, True])
async def test_explicit_assessment_can_exclude_all_or_add_to_an_unrecorded_draft(
    review_case, legacy
):
    _service, sessions, _context, packet, client, _ = review_case
    row_id = packet.rows[0].id
    body = correction()
    if legacy:
        async with sessions.begin() as session:
            stored = await session.get(PresalesRow, row_id)
            stored.draft = {k: v for k, v in stored.draft.items() if k != "prerequisites"}
        body["prerequisiteChanges"] = {"origins": [None, None, None], "excludedIndexes": []}
    else:
        body.update(prerequisites=[], conditions=[], status="supported")
        body["prerequisiteChanges"] = {"origins": [], "excludedIndexes": [0, 1]}
    result = await client.put(
        f"/api/presales/{packet.id}/rows/{row_id}/review",
        headers={"Authorization": "Bearer owner", "Idempotency-Key": "explicit-assessment"},
        json=body,
    )
    assert result.status_code == 200, result.text
    row = result.json()["rows"][0]
    assert row["review"]["prerequisites"] == body["prerequisites"]
    assert row["review"]["prerequisiteChanges"] == body["prerequisiteChanges"]
    if legacy:
        assert row["draft"]["prerequisites"] is None
    else:
        assert len(row["draft"]["prerequisites"]) == 2


async def test_reverting_human_additions_on_a_legacy_draft_requires_a_note(review_case):
    _service, sessions, _context, packet, client, calls = review_case
    row_id = packet.rows[0].id
    path = f"/api/presales/{packet.id}/rows/{row_id}/review"
    async with sessions.begin() as session:
        stored = await session.get(PresalesRow, row_id)
        stored.draft = {k: v for k, v in stored.draft.items() if k != "prerequisites"}
    body = correction()
    body["prerequisiteChanges"] = {"origins": [None, None, None], "excludedIndexes": []}
    headers = {"Authorization": "Bearer owner", "Idempotency-Key": "legacy-additions"}
    first = await client.put(path, headers=headers, json=body)
    assert first.status_code == 200, first.text
    reverted = {**body, "expectedRevision": 2, "prerequisites": None, "note": ""}
    del reverted["prerequisiteChanges"]
    headers["Idempotency-Key"] = "legacy-revert"
    rejected = await client.put(path, headers=headers, json=reverted)
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"]["code"] == "presales_review_note_required"
    current = await client.get(f"/api/presales/{packet.id}", headers=headers)
    assert current.json()["rows"][0] == first.json()["rows"][0]
    reverted["note"] = "明确撤回补录。保留原稿未记录状态及之前修订历史。"
    accepted = await client.put(path, headers=headers, json=reverted)
    assert accepted.status_code == 200, accepted.text
    row = accepted.json()["rows"][0]
    assert row["draft"]["prerequisites"] is None
    assert row["review"]["prerequisites"] is None
    assert len(row["reviewHistory"]) == 2
    assert row["reviewHistory"][0]["prerequisiteChanges"] == body["prerequisiteChanges"]
    assert len(calls) == 1


async def test_migration_preserves_legacy_readability_and_refuses_history_loss(review_case):
    service, sessions, context, packet, client, _ = review_case
    row_id = packet.rows[0].id
    path = f"/api/presales/{packet.id}/rows/{row_id}/review"
    old_body = packet.rows[0].draft.model_dump(
        mode="json", by_alias=True, exclude={"citations", "retrieval"}
    )
    old_body.update(expectedRevision=1, note="旧版结构复核。")
    old_response = await client.put(
        path,
        headers={"Authorization": "Bearer owner", "Idempotency-Key": "old-review"},
        json=old_body,
    )
    assert old_response.status_code == 200, old_response.text
    async with sessions() as session:
        old_record = await session.scalar(
            select(PresalesReview).where(PresalesReview.row_id == row_id)
        )
        old_content, old_fingerprint = old_record.content, old_record.fingerprint
        assert old_record.prerequisite_changes is None
    migration_path = (
        ROOT
        / "packages/core/src/enterprise_doc_core/db/migrations/versions"
        / "20261008_0033_presales_review_changes.py"
    )
    spec = importlib.util.spec_from_file_location("review_changes_migration", migration_path)
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
    async with sessions() as session:
        old_record = await session.scalar(
            select(PresalesReview).where(PresalesReview.row_id == row_id)
        )
        assert old_record.content == old_content and old_record.fingerprint == old_fingerprint
        assert old_record.prerequisite_changes is None
    corrected = correction()
    corrected["expectedRevision"] = 2
    result = await client.put(
        path,
        headers={"Authorization": "Bearer owner", "Idempotency-Key": "migration-review"},
        json=corrected,
    )
    assert result.status_code == 200, result.text
    with pytest.raises(RuntimeError, match="presales_review_changes_history_present"):
        async with engine.begin() as connection:
            await connection.run_sync(lambda c: migrate(c, "downgrade"))
    async with sessions() as session:
        stored = await session.scalar(
            select(PresalesReview).where(
                PresalesReview.row_id == row_id, PresalesReview.revision == 3
            )
        )
        # Exercise the actual pre-change strict parser, not a permissive replacement.
        legacy = LegacySavedReview.model_validate(stored.content)
        assert len(legacy.prerequisites) == 3
        assert stored.prerequisite_changes["excluded_indexes"] == [1]
    reloaded = await service.get(context.principal, packet.id)
    assert reloaded.rows[0].review.prerequisite_changes.excluded_indexes == [1]
