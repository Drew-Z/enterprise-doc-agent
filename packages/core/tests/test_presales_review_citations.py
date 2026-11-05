from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.review import validate_review
from enterprise_doc_core.presales.schemas import Evidence, ReviewInput, SavedDraft, SavedReview


def evidence(excerpt="原授权条款"):
    return Evidence(
        chunk_id=uuid4(),
        document_version_id=uuid4(),
        excerpt=excerpt,
        filename="采购文件.txt",
        page_number=None,
        heading=None,
        start_offset=0,
        end_offset=20,
    )


def test_review_can_replace_an_irrelevant_citation_without_changing_the_original():
    original = evidence("原稿错误选择的导航文字")
    corrected = evidence("须提交原厂盖章授权证书")
    draft = SavedDraft(
        status="supported", answer="原始答复", citations=[original], retrieval=[], prerequisites=[]
    )
    payload = ReviewInput(
        expected_revision=1,
        status="supported",
        answer="按原文更正后的答复",
        prerequisites=[],
        note="原引用与要求无关。改为对应条款",
        citations=[corrected.model_dump(include={"chunk_id", "document_version_id", "excerpt"})],
    )
    validate_review(draft, payload, None)
    assert draft.citations == [original]
    saved = SavedReview(
        **payload.model_dump(exclude={"expected_revision", "citations"}),
        citations=[corrected],
        revision=2,
        actor_id=uuid4(),
        reviewed_at=datetime.now(UTC),
    )
    assert saved.citations == [corrected]


def test_changed_evidence_needs_a_note_and_old_requests_cannot_drop_the_latest_evidence():
    draft = SavedDraft(
        status="supported",
        answer="原始答复",
        citations=[evidence()],
        retrieval=[],
        prerequisites=[],
    )
    corrected = evidence("另一条已核对原文")
    citation = corrected.model_dump(include={"chunk_id", "document_version_id", "excerpt"})
    payload = ReviewInput(
        expected_revision=1,
        status="supported",
        answer="复核答复",
        prerequisites=[],
        citations=[citation],
    )
    with pytest.raises(PresalesError, match="presales_review_note_required"):
        validate_review(draft, payload, None)
    previous = SavedReview(
        status="supported",
        answer="复核答复",
        prerequisites=[],
        citations=[corrected],
        note="更正引用",
        revision=2,
        actor_id=uuid4(),
        reviewed_at=datetime.now(UTC),
    )
    legacy = ReviewInput(
        expected_revision=2,
        status="supported",
        answer="第二次复核",
        prerequisites=[],
        note="仍需保留已更正引用",
    )
    with pytest.raises(PresalesError, match="presales_review_evidence_required"):
        validate_review(draft, legacy, previous)


def test_duplicate_explicit_review_citations_are_rejected():
    citation = evidence().model_dump(include={"chunk_id", "document_version_id", "excerpt"})
    with pytest.raises(ValidationError, match="duplicates"):
        ReviewInput(
            expected_revision=1,
            status="supported",
            answer="答复",
            citations=[citation, citation],
            note="复核",
        )
