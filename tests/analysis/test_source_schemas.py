from uuid import uuid4

import pytest
from pydantic import ValidationError

from enterprise_doc_core.analysis.schemas import AnalysisSpec, ComparisonObject, default_fields


def test_source_roles_cannot_overlap_between_objects_or_requirements() -> None:
    source = uuid4()
    with pytest.raises(ValidationError, match="one source group"):
        AnalysisSpec(
            common_requirements=(source,),
            objects=(ComparisonObject(id="a", name="供应商 A", source_version_ids=(source,)),),
            fields=default_fields(),
        )
    with pytest.raises(ValidationError, match="one source group"):
        AnalysisSpec(
            objects=(
                ComparisonObject(id="a", name="A", source_version_ids=(source,)),
                ComparisonObject(id="b", name="B", source_version_ids=(source,)),
            ),
            fields=default_fields(),
        )


def test_template_contains_ten_distinct_questions_and_accepts_empty_draft_groups() -> None:
    spec = AnalysisSpec(objects=(ComparisonObject(id="a", name="A"),), fields=default_fields())
    assert len(spec.fields) == len({item.id for item in spec.fields}) == 10
    assert all(item.question for item in spec.fields)
    assert spec.source_ids() == ()
