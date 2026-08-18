from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RagQualityCategory(StrEnum):
    FACT = "fact"
    HARD_NEGATIVE = "hard_negative"
    REFUSAL = "refusal"
    CITATION = "citation"
    SAFETY = "safety"


class RagExpectedOutcome(StrEnum):
    ANSWER = "answer"
    REFUSAL = "refusal"


class StableAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anchor_id: str = Field(min_length=1, max_length=200)
    section: str = Field(min_length=1, max_length=200)
    page: int | None = Field(default=None, ge=1)
    quote: str = Field(min_length=16, max_length=500)


class GoldenFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: str = Field(min_length=1, max_length=200)
    accepted_answers: tuple[str, ...] = Field(min_length=1)
    forbidden_answers: tuple[str, ...] = ()
    anchor_ids: tuple[str, ...] = Field(min_length=1)


class RagQualityDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_key: str = Field(min_length=1, max_length=200)
    path: str = Field(min_length=1, max_length=500)
    media_type: str = Field(min_length=1, max_length=100)
    anchors: tuple[StableAnchor, ...] = Field(min_length=1)


class RagQualityCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1, max_length=200)
    category: RagQualityCategory
    document_key: str = Field(min_length=1, max_length=200)
    query: str = Field(min_length=1, max_length=20_000)
    expected_outcome: RagExpectedOutcome
    facts: tuple[GoldenFact, ...] = ()
    expected_anchor_ids: tuple[str, ...] = ()
    accepted_refusal_codes: tuple[str, ...] = ()
    trial: bool = False

    @model_validator(mode="after")
    def validate_outcome_contract(self) -> Self:
        if self.expected_outcome is RagExpectedOutcome.REFUSAL:
            if self.facts or self.expected_anchor_ids:
                raise ValueError("refusal cases cannot require answer facts or citations")
            if not self.accepted_refusal_codes:
                raise ValueError("refusal cases require accepted refusal codes")
        elif not self.facts and not self.expected_anchor_ids:
            raise ValueError("answer cases require facts or expected citations")
        return self


class RagQualityDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1)
    version: str = Field(min_length=1, max_length=200)
    corpus_root: str = Field(min_length=1, max_length=200)
    expected_category_counts: dict[RagQualityCategory, int] = Field(default_factory=dict)
    documents: tuple[RagQualityDocument, ...] = Field(min_length=1)
    cases: tuple[RagQualityCase, ...] = Field(min_length=1)
    targets: dict[str, float] = Field(default_factory=dict)
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        document_keys = [document.document_key for document in self.documents]
        if len(set(document_keys)) != len(document_keys):
            raise ValueError("document_key values must be unique")
        case_ids = [case.case_id for case in self.cases]
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("case_id values must be unique")
        anchors: dict[str, StableAnchor] = {}
        anchor_documents: dict[str, str] = {}
        for document in self.documents:
            for anchor in document.anchors:
                if anchor.anchor_id in anchors:
                    raise ValueError("anchor_id values must be unique")
                anchors[anchor.anchor_id] = anchor
                anchor_documents[anchor.anchor_id] = document.document_key
        documents = set(document_keys)
        for case in self.cases:
            if case.document_key not in documents:
                raise ValueError(f"case references unknown document: {case.document_key}")
            case_anchor_ids = set(case.expected_anchor_ids)
            if len(case_anchor_ids) != len(case.expected_anchor_ids):
                raise ValueError(f"case has duplicate expected anchors: {case.case_id}")
            for anchor_id in case_anchor_ids:
                if anchor_id not in anchors:
                    raise ValueError(f"case references unknown anchor: {anchor_id}")
                if anchor_documents[anchor_id] != case.document_key:
                    raise ValueError(f"case anchor belongs to another document: {anchor_id}")
            fact_ids: list[str] = []
            for fact in case.facts:
                fact_ids.append(fact.fact_id)
                for anchor_id in fact.anchor_ids:
                    if anchor_id not in anchors:
                        raise ValueError(f"fact references unknown anchor: {anchor_id}")
                    if anchor_documents[anchor_id] != case.document_key:
                        raise ValueError(f"fact anchor belongs to another document: {anchor_id}")
            if len(set(fact_ids)) != len(fact_ids):
                raise ValueError(f"fact_id values must be unique within case: {case.case_id}")
        actual_counts = {
            category: sum(case.category is category for case in self.cases)
            for category in RagQualityCategory
        }
        for category, expected in self.expected_category_counts.items():
            if expected < 0:
                raise ValueError("expected category counts cannot be negative")
            if actual_counts[category] != expected:
                raise ValueError(
                    f"category count mismatch for {category.value}: "
                    f"expected {expected}, got {actual_counts[category]}"
                )
        return self

    @property
    def documents_by_key(self) -> dict[str, RagQualityDocument]:
        return {document.document_key: document for document in self.documents}

    @property
    def cases_by_id(self) -> dict[str, RagQualityCase]:
        return {case.case_id: case for case in self.cases}

    @property
    def anchors_by_id(self) -> dict[str, StableAnchor]:
        return {
            anchor.anchor_id: anchor for document in self.documents for anchor in document.anchors
        }


@dataclass(frozen=True, slots=True)
class LoadedRagQualityDataset:
    dataset: RagQualityDataset
    documents: dict[str, bytes]
    dataset_sha256: str
    corpus_sha256: str


class ObservedCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_chunk_id: str = Field(min_length=1, max_length=200)
    document_key: str = Field(min_length=1, max_length=200)
    page: int | None = Field(default=None, ge=1)
    heading: str | None = Field(default=None, max_length=200)
    excerpt: str = Field(min_length=1, max_length=500)


class RagQualityObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    terminal_status: str = Field(min_length=1, max_length=64)
    answer_text: str | None = None
    citations: tuple[ObservedCitation, ...] = ()
    error_code: str | None = Field(default=None, max_length=100)
    duration_ms: float = Field(ge=0)


@dataclass(frozen=True, slots=True)
class RagQualityCitationDiagnostic:
    ordinal: int
    resolved_anchor_ids: tuple[str, ...]

    @property
    def resolved(self) -> bool:
        return bool(self.resolved_anchor_ids)


@dataclass(frozen=True, slots=True)
class RagQualityCaseScore:
    case_id: str
    passed: bool
    expected_refusal: bool
    predicted_refusal: bool
    fact_recall: float | None
    closed_label_fact_precision: float | None
    grounded_fact_rate: float | None
    citation_precision: float | None
    citation_recall: float | None
    refusal_reason_correct: bool | None
    matched_fact_ids: tuple[str, ...]
    forbidden_fact_ids: tuple[str, ...]
    matched_anchor_ids: tuple[str, ...]
    citation_diagnostics: tuple[RagQualityCitationDiagnostic, ...]
    unresolved_citation_count: int
    unexpected_anchor_ids: tuple[str, ...]
    duration_ms: float
    terminal_status: str
    error_code: str | None


@dataclass(frozen=True, slots=True)
class RagQualityAggregate:
    case_count: int
    passed_case_count: int
    answer_case_count: int
    refusal_case_count: int
    fact_recall: float | None
    closed_label_fact_precision: float | None
    grounded_fact_rate: float | None
    citation_precision: float | None
    citation_recall: float | None
    refusal_precision: float | None
    refusal_recall: float | None
    refusal_reason_accuracy: float | None
    duration_ms: tuple[float, ...]


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def _contains_variant(text: str, variants: tuple[str, ...]) -> bool:
    normalized = _normalize(text)
    for variant in variants:
        normalized_variant = _normalize(variant)
        if not normalized_variant:
            continue
        if normalized_variant[0].isdigit():
            # A decimal component is not a standalone numeric answer: for example,
            # "5 percent" must not match the threshold "99.5 percent".
            left_boundary = r"(?<![\w.])"
        else:
            left_boundary = r"(?<!\w)" if normalized_variant[0].isalnum() else ""
        right_boundary = r"(?!\w)" if normalized_variant[-1].isalnum() else ""
        if re.search(
            f"{left_boundary}{re.escape(normalized_variant)}{right_boundary}",
            normalized,
        ):
            return True
    return False


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _corpus_hash(documents: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for key in sorted(documents):
        digest.update(key.encode("utf-8"))
        digest.update(b"\0")
        digest.update(documents[key])
        digest.update(b"\0")
    return digest.hexdigest()


def load_rag_quality_dataset(path: Path) -> LoadedRagQualityDataset:
    try:
        raw_bytes = path.read_bytes()
        payload = json.loads(raw_bytes)
        dataset = RagQualityDataset.model_validate(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("unable to read RAG quality dataset") from error
    corpus_root = (path.parent / dataset.corpus_root).resolve()
    documents: dict[str, bytes] = {}
    for document in dataset.documents:
        candidate = (corpus_root / document.path).resolve()
        try:
            candidate.relative_to(corpus_root)
        except ValueError as error:
            raise ValueError("document paths must remain inside corpus_root") from error
        try:
            content = candidate.read_bytes()
            decoded = content.decode("utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise ValueError(f"unable to read corpus document: {document.document_key}") from error
        normalized_content = _normalize(decoded)
        for anchor in document.anchors:
            if _normalize(anchor.quote) not in normalized_content:
                raise ValueError(f"quote was not found for anchor: {anchor.anchor_id}")
        documents[document.document_key] = content
    canonical = json.dumps(
        dataset.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return LoadedRagQualityDataset(
        dataset=dataset,
        documents=documents,
        dataset_sha256=_sha256(canonical),
        corpus_sha256=_corpus_hash(documents),
    )


def _resolve_anchors(
    dataset: RagQualityDataset,
    *,
    citation: ObservedCitation,
) -> tuple[str, ...]:
    document = dataset.documents_by_key.get(citation.document_key)
    if document is None:
        return ()
    normalized_excerpt = _normalize(citation.excerpt)
    return tuple(
        anchor.anchor_id
        for anchor in document.anchors
        if not (
            anchor.page is not None and citation.page is not None and anchor.page != citation.page
        )
        and (
            _normalize(anchor.quote) in normalized_excerpt
            or normalized_excerpt in _normalize(anchor.quote)
        )
    )


def score_rag_quality_case(
    dataset: RagQualityDataset,
    case: RagQualityCase,
    observation: RagQualityObservation,
) -> RagQualityCaseScore:
    expected_refusal = case.expected_outcome is RagExpectedOutcome.REFUSAL
    predicted_refusal = observation.terminal_status in {"refused", "rejected"}
    answer = observation.answer_text or ""
    matched_facts = tuple(
        fact.fact_id for fact in case.facts if _contains_variant(answer, fact.accepted_answers)
    )
    forbidden_facts = tuple(
        fact.fact_id for fact in case.facts if _contains_variant(answer, fact.forbidden_answers)
    )
    expected_fact_count = len(case.facts)
    fact_recall = len(matched_facts) / expected_fact_count if expected_fact_count else None
    precision_denominator = len(matched_facts) + len(forbidden_facts)
    closed_label_precision = (
        len(matched_facts) / precision_denominator
        if precision_denominator
        else (0.0 if expected_fact_count else None)
    )
    citation_anchor_ids = tuple(
        _resolve_anchors(dataset, citation=citation) for citation in observation.citations
    )
    citation_diagnostics = tuple(
        RagQualityCitationDiagnostic(ordinal=ordinal, resolved_anchor_ids=resolved_anchor_ids)
        for ordinal, resolved_anchor_ids in enumerate(citation_anchor_ids, start=1)
    )
    matched_anchor_ids = tuple(
        dict.fromkeys(
            anchor_id
            for resolved_anchor_ids in citation_anchor_ids
            for anchor_id in resolved_anchor_ids
        )
    )
    expected_anchor_ids = set(case.expected_anchor_ids)
    unexpected_anchor_ids = tuple(
        anchor_id for anchor_id in matched_anchor_ids if anchor_id not in expected_anchor_ids
    )
    correct_anchor_ids = expected_anchor_ids.intersection(matched_anchor_ids)
    predicted_anchor_count = sum(
        len(resolved_anchor_ids) if resolved_anchor_ids else 1
        for resolved_anchor_ids in citation_anchor_ids
    )
    correct_anchor_prediction_count = sum(
        len(expected_anchor_ids.intersection(resolved_anchor_ids))
        for resolved_anchor_ids in citation_anchor_ids
    )
    citation_precision = (
        correct_anchor_prediction_count / predicted_anchor_count
        if predicted_anchor_count
        else (0.0 if expected_anchor_ids else 1.0)
    )
    citation_recall = (
        len(correct_anchor_ids) / len(expected_anchor_ids) if expected_anchor_ids else 1.0
    )
    grounded_fact_ids = {
        fact.fact_id
        for fact in case.facts
        if fact.fact_id in matched_facts
        and expected_anchor_ids.intersection(fact.anchor_ids).intersection(matched_anchor_ids)
    }
    grounded_fact_rate = len(grounded_fact_ids) / len(matched_facts) if matched_facts else 0.0
    refusal_reason_correct: bool | None = None
    if expected_refusal:
        refusal_reason_correct = (
            predicted_refusal and observation.error_code in case.accepted_refusal_codes
        )
    if expected_refusal:
        passed = predicted_refusal and refusal_reason_correct is True
    else:
        passed = (
            not predicted_refusal
            and (fact_recall is None or fact_recall == 1.0)
            and (closed_label_precision is None or closed_label_precision == 1.0)
            and (grounded_fact_rate is None or grounded_fact_rate == 1.0)
            and (citation_recall is None or citation_recall == 1.0)
        )
    return RagQualityCaseScore(
        case_id=case.case_id,
        passed=passed,
        expected_refusal=expected_refusal,
        predicted_refusal=predicted_refusal,
        fact_recall=fact_recall,
        closed_label_fact_precision=closed_label_precision,
        grounded_fact_rate=grounded_fact_rate,
        citation_precision=citation_precision,
        citation_recall=citation_recall,
        refusal_reason_correct=refusal_reason_correct,
        matched_fact_ids=matched_facts,
        forbidden_fact_ids=forbidden_facts,
        matched_anchor_ids=matched_anchor_ids,
        citation_diagnostics=citation_diagnostics,
        unresolved_citation_count=sum(
            not diagnostic.resolved for diagnostic in citation_diagnostics
        ),
        unexpected_anchor_ids=unexpected_anchor_ids,
        duration_ms=observation.duration_ms,
        terminal_status=observation.terminal_status,
        error_code=observation.error_code,
    )


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def aggregate_rag_quality_scores(
    scores: tuple[RagQualityCaseScore, ...],
) -> RagQualityAggregate:
    if not scores:
        raise ValueError("at least one RAG quality score is required")
    answer_scores = [score for score in scores if not score.expected_refusal]
    refusal_scores = [score for score in scores if score.expected_refusal]
    predicted_refusals = sum(score.predicted_refusal for score in scores)
    true_positive_refusals = sum(
        score.expected_refusal and score.predicted_refusal for score in scores
    )
    expected_refusals = len(refusal_scores)
    correct_reasons = sum(score.refusal_reason_correct is True for score in refusal_scores)
    return RagQualityAggregate(
        case_count=len(scores),
        passed_case_count=sum(score.passed for score in scores),
        answer_case_count=len(answer_scores),
        refusal_case_count=expected_refusals,
        fact_recall=_mean(
            [score.fact_recall for score in answer_scores if score.fact_recall is not None]
        ),
        closed_label_fact_precision=_mean(
            [
                score.closed_label_fact_precision
                for score in answer_scores
                if score.closed_label_fact_precision is not None
            ]
        ),
        grounded_fact_rate=_mean(
            [
                score.grounded_fact_rate
                for score in answer_scores
                if score.grounded_fact_rate is not None
            ]
        ),
        citation_precision=_mean(
            [
                score.citation_precision
                for score in answer_scores
                if score.citation_precision is not None
            ]
        ),
        citation_recall=_mean(
            [score.citation_recall for score in answer_scores if score.citation_recall is not None]
        ),
        refusal_precision=(
            true_positive_refusals / predicted_refusals if predicted_refusals else None
        ),
        refusal_recall=(true_positive_refusals / expected_refusals if expected_refusals else None),
        refusal_reason_accuracy=(
            correct_reasons / expected_refusals if expected_refusals else None
        ),
        duration_ms=tuple(score.duration_ms for score in scores),
    )


__all__ = [
    "GoldenFact",
    "LoadedRagQualityDataset",
    "ObservedCitation",
    "RagExpectedOutcome",
    "RagQualityAggregate",
    "RagQualityCase",
    "RagQualityCaseScore",
    "RagQualityCategory",
    "RagQualityCitationDiagnostic",
    "RagQualityDataset",
    "RagQualityDocument",
    "RagQualityObservation",
    "StableAnchor",
    "aggregate_rag_quality_scores",
    "load_rag_quality_dataset",
    "score_rag_quality_case",
]
