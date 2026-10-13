from __future__ import annotations

import hashlib
import re
from datetime import date
from decimal import Decimal
from uuid import UUID

from enterprise_doc_core.analysis.execution_schemas import (
    CalculationReceipt,
    Candidate,
    CellResult,
    Duration,
    EvidenceSet,
    EvidenceWindow,
    Money,
    ResolvedEvidence,
)
from enterprise_doc_core.analysis.schemas import AnalysisField, FrozenSource
from enterprise_doc_core.analysis.service import AnalysisError
from enterprise_doc_core.documents.structure import DocumentBlockData, ParsedDocumentStructure


def select_evidence(
    field: AnalysisField,
    sources: tuple[tuple[FrozenSource, ParsedDocumentStructure], ...],
) -> EvidenceSet:
    """Version-pinned lexical baseline. Never follows the mutable active generation.

    All blocks in the authorized object are scored locally; at most eight windows
    enter the model. No embedding request or implicit cross-object search is made.
    """
    hints = {
        "scope": "features services included projects tasks tools",
        "pricing_unit": "per user month annually billing users fees",
        "recurring_cost": "price pricing cost fees month annual USD",
        "setup_fee": "setup implementation onboarding installation fee",
        "tax_basis": "tax taxes inclusive exclusive duties VAT",
        "lead_time": "delivery implementation onboarding days start",
        "contract_term": "subscription term renewal termination cancel",
        "support": "support response hours priority",
        "exclusions": "limitations excluded restrictions trial",
        "payment_terms": "payment advance due invoice refund credit",
    }
    query = f"{field.name} {field.question} {hints.get(field.id, '')}".lower()
    terms = set(re.findall(r"[a-z0-9]{2,}|[\u4e00-\u9fff]{2}", query))
    ranked = []
    for source, parsed in sources:
        for block in parsed.blocks:
            score = sum(term in block.text.lower() for term in terms)
            ranked.append((score, source, block))
    ranked.sort(key=lambda item: (-item[0], str(item[1].document_version_id), item[2].index))
    # Start with the best block from every file, then add global hits and neighbors.
    # This prevents a verbose price page from starving a separate terms attachment.
    best: dict[UUID, tuple[int, FrozenSource, DocumentBlockData]] = {}
    for item in ranked:
        best.setdefault(item[1].document_version_id, item)
    anchors = list(best.values()) + ranked
    # Include neighboring context (table headers and continuation clauses where adjacent).
    selected: list[EvidenceWindow] = []
    seen = set()
    by_source = {source.document_version_id: parsed for source, parsed in sources}
    for position, (_score, source, block) in enumerate(anchors):
        indices = (
            (block.index,)
            if position < len(best)
            else (block.index, block.index - 1, block.index + 1)
        )
        for index in indices:
            blocks = by_source[source.document_version_id].blocks
            if index < 0 or index >= len(blocks):
                continue
            candidate = blocks[index]
            key = (source.document_version_id, candidate.block_id)
            if key in seen:
                continue
            seen.add(key)
            selected.append(
                EvidenceWindow(
                    id=f"e{len(selected) + 1}",
                    document_version_id=source.document_version_id,
                    generation_id=source.generation_id,
                    block_id=candidate.block_id,
                    block_hash=candidate.content_sha256,
                    text=candidate.text[:2000],
                    locator=candidate.locator,
                    filename=source.filename,
                    coverage=source.coverage,
                    formula_unverified=candidate.formula is not None,
                )
            )
            if len(selected) == 8:
                break
        if len(selected) == 8:
            break
    coverage = "complete"
    if any(source.coverage == "partial" for source, _ in sources):
        coverage = "partial"
    elif any(source.coverage == "unknown" for source, _ in sources):
        coverage = "unknown"
    return EvidenceSet(
        windows=tuple(selected),
        searched_versions=tuple(s.document_version_id for s, _ in sources),
        searched_blocks=len(ranked),
        returned_blocks=len(selected),
        coverage=coverage,
    )


def validate_value_type(value: object, field: AnalysisField) -> None:
    if value is not None:
        valid = {
            "text": isinstance(value, str),
            "enum": isinstance(value, str),
            "boolean": type(value) is bool,
            "money": isinstance(value, Money),
            "duration": isinstance(value, Duration),
            "list": isinstance(value, tuple),
            "decimal": isinstance(value, str)
            and bool(re.fullmatch(r"-?(0|[1-9][0-9]{0,17})(\.[0-9]{1,8})?", value)),
            "date": isinstance(value, str) and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)),
        }[field.value_type]
        if not valid:
            raise AnalysisError("analysis_value_type_invalid")
        if field.value_type == "date" and isinstance(value, str):
            try:
                date.fromisoformat(value)
            except ValueError as error:
                raise AnalysisError("analysis_value_type_invalid") from error


def validate_candidate(
    candidate: Candidate,
    field: AnalysisField,
    windows: tuple[EvidenceWindow, ...],
    *,
    calculations: tuple[CalculationReceipt, ...] = (),
) -> CellResult:
    allowed = {item.id: item for item in windows}
    resolved = []
    seen = set()
    citations = candidate.citations + tuple(
        c for condition in candidate.conditions for c in condition.citations
    )
    for citation in citations:
        window = allowed.get(citation.evidence_id)
        if window is None or citation.quote not in window.text:
            raise AnalysisError("analysis_citation_invalid")
        if window.formula_unverified and candidate.evidence_state == "supported":
            raise AnalysisError("analysis_formula_unverified")
        key = (citation.evidence_id, citation.quote)
        if key in seen:
            continue
        seen.add(key)
        resolved.append(
            ResolvedEvidence(
                evidence_id=window.id,
                document_version_id=window.document_version_id,
                generation_id=window.generation_id,
                block_id=window.block_id,
                block_hash=window.block_hash,
                quote=citation.quote,
                quote_hash=hashlib.sha256(citation.quote.encode()).hexdigest(),
                locator=window.locator,
                filename=window.filename,
            )
        )
    if candidate.evidence_state == "conflicting" and len(seen) < 2:
        raise AnalysisError("analysis_citation_invalid")
    value = candidate.value
    validate_value_type(value, field)
    selected_calculations: tuple[CalculationReceipt, ...] = ()
    if candidate.calculation_id is not None:
        selected_calculations = tuple(
            item for item in calculations if item.id == candidate.calculation_id
        )
        if len(selected_calculations) != 1:
            raise AnalysisError("analysis_calculation_invalid")
        receipt = selected_calculations[0]
        if isinstance(value, Money):
            matches = (
                Decimal(value.amount) == Decimal(receipt.result)
                and value.currency == receipt.result_unit
            )
        elif isinstance(value, str) and field.value_type == "decimal":
            matches = Decimal(value) == Decimal(receipt.result)
        else:
            matches = False
        if not matches:
            raise AnalysisError("analysis_calculation_invalid")
    return CellResult(
        **candidate.model_dump(),
        evidence=tuple(resolved),
        calculations=selected_calculations,
    )
