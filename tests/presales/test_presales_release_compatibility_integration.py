from __future__ import annotations

import copy

import pytest
from sqlalchemy import select

from enterprise_doc_core.presales.background import BackgroundGeneration
from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.models import PresalesAttempt, PresalesReview
from enterprise_doc_core.presales.schemas import ReviewInput
from tests.browser_sessions.conftest import browser_db as browser_db
from tests.presales.legacy_rc40_reader import LegacyRc40Reader
from tests.presales.test_presales_review_changes_integration import (
    correction,
)
from tests.presales.test_presales_review_changes_integration import (
    review_case as review_case,
)
from tests.presales.test_presales_workflow_integration import workspace as workspace

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("mode", ["legacy", "deep"])
async def test_frozen_rc40_reader_preserves_new_schema_history_and_tenant_boundary(
    review_case, workspace, mode
):
    service, sessions, context, packet, _, calls = review_case
    if mode == "deep":
        service.generation.settings.background_generation_enabled = True
        packet = await service.create(context.principal, workspace[-1], "deep-compatibility")
        await service.generate(
            context.principal, packet.id, packet.rows[0].id, "deep-once", execution_mode="deep"
        )
        worker = BackgroundGeneration(service.generation, {"primary": service.generation.gateway})
        assert await worker.run_once("compatibility-worker")
        packet = await service.get(context.principal, packet.id)
        assert packet.rows[0].attempts[0].execution_policy.mode == "deep"
    row_id = packet.rows[0].id
    first = correction()
    await service.review(
        context.principal, packet.id, row_id, ReviewInput.model_validate(first), "first-correction"
    )
    second = copy.deepcopy(first)
    second["expectedRevision"] = 2
    second["prerequisites"][1]["condition"] = "验收记录已复核。"
    second["conditions"] = ["验收记录已复核。"]
    current = await service.review(
        context.principal, packet.id, row_id, ReviewInput.model_validate(second), "next-correction"
    )

    async def history():
        async with sessions() as session:
            attempts = (
                await session.execute(
                    select(PresalesAttempt.id, PresalesAttempt.execution_policy)
                    .where(PresalesAttempt.row_id == row_id)
                    .order_by(PresalesAttempt.number)
                )
            ).all()
            reviews = (
                await session.execute(
                    select(PresalesReview.content, PresalesReview.prerequisite_changes)
                    .where(PresalesReview.row_id == row_id)
                    .order_by(PresalesReview.revision)
                )
            ).all()
            return attempts, reviews

    before = await history()
    reader = LegacyRc40Reader(
        sessions, background=service.generation.settings.background_generation_enabled
    )
    legacy = await reader.get(context.principal, packet.id)
    expected = current.model_dump(
        mode="json",
        exclude={
            "available_execution_modes": True,
            "rows": {
                "__all__": {
                    "attempts": {"__all__": {"execution_policy"}},
                    "review": {"prerequisite_changes"},
                    "review_history": {"__all__": {"prerequisite_changes"}},
                }
            },
        },
    )
    assert legacy.model_dump(mode="json") == expected
    assert len(legacy.rows[0].review_history) == 2
    assert len(legacy.rows[0].review.prerequisites) == 3
    with pytest.raises(PresalesError) as denied:
        await reader.get(workspace[3].principal, packet.id)
    assert denied.value.code == "presales_not_found"
    assert await history() == before
    assert len(calls) == (2 if mode == "deep" else 1)
