import pytest
from pydantic import ValidationError

from enterprise_doc_core.presales.schemas import ModelDraft, ReviewInput


@pytest.mark.parametrize("count", [13, 14, 156])
def test_public_draft_and_review_keep_independent_gaps(count):
    gaps = [f"确认第{i}项实际情况。" for i in range(count)]
    draft = ModelDraft(
        status="insufficient_evidence", answer="实际情况待确认。", missing_information=gaps
    )
    review = ReviewInput(**draft.model_dump(exclude={"citations"}), expected_revision=1)
    assert draft.missing_information == review.missing_information == gaps


@pytest.mark.parametrize("gaps", [["确" * 1001], ["确认"] * 157, ["确" * 500] * 24 + ["认"]])
def test_public_information_budget_rejects_without_truncation(gaps):
    with pytest.raises(ValidationError):
        ModelDraft(status="insufficient_evidence", answer="待确认。", missing_information=gaps)


def test_total_budget_counts_unicode_characters_and_accepts_old_maximum():
    gaps = ["𠀀" * 1000] * 12
    draft = ModelDraft(status="insufficient_evidence", answer="待确认。", missing_information=gaps)
    assert draft.missing_information == gaps
