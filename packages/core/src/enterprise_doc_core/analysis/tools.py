from __future__ import annotations

import re
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation

from enterprise_doc_core.analysis.execution_schemas import (
    CalculateRequest,
    CalculationReceipt,
    EvidenceSet,
    EvidenceWindow,
    ReadSourceRequest,
    ResolvedCalculationOperand,
    SearchSourcesRequest,
)
from enterprise_doc_core.analysis.schemas import FrozenSource
from enterprise_doc_core.documents.structure import DocumentBlockData, ParsedDocumentStructure

FrozenStructures = tuple[tuple[FrozenSource, ParsedDocumentStructure], ...]


class ToolExecutionError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ToolInputRequired(ToolExecutionError):
    def __init__(self, parameters: tuple[str, ...]) -> None:
        self.parameters = parameters
        super().__init__("analysis_tool_input_required")


def _window(source: FrozenSource, block: DocumentBlockData, evidence_id: str) -> EvidenceWindow:
    return EvidenceWindow(
        id=evidence_id,
        document_version_id=source.document_version_id,
        generation_id=source.generation_id,
        block_id=block.block_id,
        block_hash=block.content_sha256,
        text=block.text[:2000],
        locator=block.locator,
        filename=source.filename,
        coverage=source.coverage,
        formula_unverified=block.formula is not None,
    )


def _evidence_set(windows: list[EvidenceWindow], sources: FrozenStructures) -> EvidenceSet:
    coverage = "complete"
    if any(source.coverage == "partial" for source, _ in sources):
        coverage = "partial"
    elif any(source.coverage == "unknown" for source, _ in sources):
        coverage = "unknown"
    return EvidenceSet(
        windows=tuple(windows),
        searched_versions=tuple(source.document_version_id for source, _ in sources),
        searched_blocks=sum(len(parsed.blocks) for _, parsed in sources),
        returned_blocks=len(windows),
        coverage=coverage,
    )


def search_sources(
    request: SearchSourcesRequest,
    sources: FrozenStructures,
    *,
    evidence_prefix: str,
) -> EvidenceSet:
    terms = tuple(
        dict.fromkeys(re.findall(r"[a-z0-9]{2,}|[\u4e00-\u9fff]{2}", request.query.lower()))
    )
    if not terms:
        raise ToolExecutionError("analysis_search_query_invalid")
    ranked = []
    for source, parsed in sources:
        for block in parsed.blocks:
            normalized = block.text.lower()
            score = sum(normalized.count(term) for term in terms)
            if score:
                ranked.append((score, source, block))
    ranked.sort(key=lambda item: (-item[0], str(item[1].document_version_id), item[2].index))
    windows = [
        _window(source, block, f"{evidence_prefix}_{index}")
        for index, (_, source, block) in enumerate(ranked[: request.max_results], 1)
    ]
    return _evidence_set(windows, sources)


def read_source(
    request: ReadSourceRequest,
    sources: FrozenStructures,
    *,
    evidence_prefix: str,
) -> EvidenceSet:
    selected = next(
        (
            (source, parsed)
            for source, parsed in sources
            if source.document_version_id == request.document_version_id
        ),
        None,
    )
    if selected is None:
        raise ToolExecutionError("analysis_tool_scope_invalid")
    source, parsed = selected
    target = next((block for block in parsed.blocks if block.block_id == request.block_id), None)
    if target is None:
        raise ToolExecutionError("analysis_source_locator_invalid")
    start = max(0, target.index - request.before)
    stop = min(len(parsed.blocks), target.index + request.after + 1)
    windows = [
        _window(source, block, f"{evidence_prefix}_{index}")
        for index, block in enumerate(parsed.blocks[start:stop], 1)
    ]
    return _evidence_set(windows, ((source, parsed),))


def calculate(
    request: CalculateRequest,
    *,
    evidence_ids: set[str],
    inputs: dict[str, str],
    calculation_id: str,
) -> CalculationReceipt:
    missing = tuple(
        operand.parameter
        for operand in request.operands
        if operand.parameter is not None and operand.parameter not in inputs
    )
    if missing:
        raise ToolInputRequired(missing)

    resolved: list[ResolvedCalculationOperand] = []
    values: list[Decimal] = []
    for operand in request.operands:
        if operand.value is not None:
            if operand.evidence_id not in evidence_ids:
                raise ToolExecutionError("analysis_calculation_evidence_invalid")
            raw = operand.value
            source = f"evidence:{operand.evidence_id}"
        else:
            assert operand.parameter is not None
            raw = inputs[operand.parameter].strip()
            source = f"user_input:{operand.parameter}"
        try:
            value = Decimal(raw)
        except InvalidOperation as error:
            raise ToolExecutionError("analysis_calculation_input_invalid") from error
        if not value.is_finite():
            raise ToolExecutionError("analysis_calculation_input_invalid")
        values.append(value)
        resolved.append(
            ResolvedCalculationOperand(
                name=operand.name,
                value=format(value, "f"),
                unit=operand.unit,
                source=source,
            )
        )

    if request.operation == "sum":
        if any(operand.unit != request.result_unit for operand in request.operands):
            raise ToolExecutionError("analysis_calculation_unit_invalid")
        result = sum(values, Decimal(0))
        separator = " + "
    elif request.operation in {"product", "explicit_period_total"}:
        if request.operands[0].unit != request.result_unit:
            raise ToolExecutionError("analysis_calculation_unit_invalid")
        result = Decimal(1)
        for value in values:
            result *= value
        separator = " * "
    elif request.operation == "unit_conversion":
        if len(request.operands) != 1 or request.operands[0].unit != request.result_unit:
            raise ToolExecutionError("analysis_calculation_unit_invalid")
        result = values[0]
        separator = ""
    else:  # pragma: no cover - Pydantic rejects unknown operations.
        raise ToolExecutionError("analysis_calculation_operation_invalid")

    rounding = {
        "half_even": ROUND_HALF_EVEN,
        "half_up": ROUND_HALF_UP,
        "down": ROUND_DOWN,
    }[request.rounding]
    quantum = Decimal(1).scaleb(-request.decimal_places)
    try:
        rounded = result.quantize(quantum, rounding=rounding)
    except InvalidOperation as error:
        raise ToolExecutionError("analysis_calculation_result_invalid") from error
    return CalculationReceipt(
        id=calculation_id,
        operation=request.operation,
        expression=separator.join(item.value for item in resolved),
        operands=tuple(resolved),
        result=format(rounded, f".{request.decimal_places}f"),
        result_unit=request.result_unit,
        rounding=request.rounding,
        decimal_places=request.decimal_places,
    )
