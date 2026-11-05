from uuid import uuid4

import pytest

from enterprise_doc_core.analysis.execution_schemas import (
    CalculateRequest,
    CalculationOperand,
    ReadSourceRequest,
    SearchSourcesRequest,
)
from enterprise_doc_core.analysis.schemas import FrozenSource
from enterprise_doc_core.analysis.tools import (
    ToolExecutionError,
    ToolInputRequired,
    calculate,
    read_source,
    search_sources,
)
from enterprise_doc_core.documents.structure import parse_document_structure


def source():
    content = "\n".join(
        [f"General service note {index}" for index in range(10)]
        + ["Cancellation requires 30 days written notice."]
    ).encode()
    parsed = parse_document_structure(content, extension=".txt")
    frozen = FrozenSource(
        document_version_id=uuid4(),
        generation_id=uuid4(),
        object_id="supplier_a",
        filename="terms.txt",
        source_sha256=parsed.source_sha256,
        structure_sha256="a" * 64,
        coverage="complete",
        block_count=len(parsed.blocks),
        issues=(),
    )
    return frozen, parsed


def test_search_and_read_stay_inside_the_frozen_source_scope():
    frozen, parsed = source()
    searched = search_sources(
        SearchSourcesRequest(tool="search_sources", query="written notice", max_results=2),
        ((frozen, parsed),),
        evidence_prefix="t1",
    )
    assert len(searched.windows) == 1
    assert searched.windows[0].text == "Cancellation requires 30 days written notice."

    read = read_source(
        ReadSourceRequest(
            tool="read_source",
            document_version_id=frozen.document_version_id,
            block_id=searched.windows[0].block_id,
            before=1,
        ),
        ((frozen, parsed),),
        evidence_prefix="t2",
    )
    assert [window.text for window in read.windows][-1] == searched.windows[0].text

    with pytest.raises(ToolExecutionError, match="analysis_tool_scope_invalid"):
        read_source(
            ReadSourceRequest(
                tool="read_source",
                document_version_id=uuid4(),
                block_id=searched.windows[0].block_id,
            ),
            ((frozen, parsed),),
            evidence_prefix="t3",
        )


def test_decimal_calculation_requires_declared_inputs_units_and_rounding():
    request = CalculateRequest(
        tool="calculate",
        operation="explicit_period_total",
        operands=(
            CalculationOperand(name="monthly_price", value="19.995", unit="CNY", evidence_id="e1"),
            CalculationOperand(name="users", parameter="user_count", unit="seat"),
            CalculationOperand(name="months", parameter="month_count", unit="month"),
        ),
        result_unit="CNY",
        rounding="half_even",
        decimal_places=2,
    )
    with pytest.raises(ToolInputRequired) as missing:
        calculate(request, evidence_ids={"e1"}, inputs={}, calculation_id="c1")
    assert missing.value.parameters == ("user_count", "month_count")

    receipt = calculate(
        request,
        evidence_ids={"e1"},
        inputs={"user_count": "3", "month_count": "12"},
        calculation_id="c1",
    )
    assert receipt.result == "719.82"
    assert receipt.result_unit == "CNY"
    assert [operand.source for operand in receipt.operands] == [
        "evidence:e1",
        "user_input:user_count",
        "user_input:month_count",
    ]

    with pytest.raises(ToolExecutionError, match="analysis_calculation_unit_invalid"):
        calculate(
            request.model_copy(update={"result_unit": "USD"}),
            evidence_ids={"e1"},
            inputs={"user_count": "3", "month_count": "12"},
            calculation_id="c2",
        )
