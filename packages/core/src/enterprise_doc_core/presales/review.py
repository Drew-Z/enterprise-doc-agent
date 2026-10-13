from __future__ import annotations

from pydantic import ValidationError

from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.schemas import (
    CitationInput,
    ModelDraft,
    PrerequisiteChanges,
    ReviewInput,
    SavedDraft,
    SavedReview,
    review_citations,
)


def validate_review(draft: SavedDraft, payload: ReviewInput, previous: SavedReview | None) -> None:
    if payload.citations is None and previous is not None and previous.citations is not None:
        raise PresalesError("presales_review_evidence_required")
    original_citations = [
        CitationInput(**c.model_dump(include={"chunk_id", "document_version_id", "excerpt"}))
        for c in draft.citations
    ]
    previous_citations = [
        CitationInput(**c.model_dump(include={"chunk_id", "document_version_id", "excerpt"}))
        for c in review_citations(draft, previous)
    ]
    citations = payload.citations if payload.citations is not None else original_citations
    original, proposed = draft.prerequisites, payload.prerequisites
    changes = payload.prerequisite_changes
    if changes is None:
        # Old requests keep their original strict contract and replay fingerprints.
        if (original is None) != (proposed is None) or [
            (p.condition, p.citation_indexes) for p in original or []
        ] != [(p.condition, p.citation_indexes) for p in proposed or []]:
            raise PresalesError("presales_review_prerequisites_invalid")
    else:
        try:
            changes.validate_originals(len(original or []))
        except ValueError as error:
            raise PresalesError("presales_review_prerequisites_invalid") from error
    original_mapping = PrerequisiteChanges(origins=list(range(len(original or []))))
    previous_mapping = (
        previous.prerequisite_changes
        if previous is not None and previous.prerequisite_changes is not None
        else original_mapping
    )
    effective_mapping = changes or original_mapping
    if not payload.note.strip() and (
        proposed != original
        or proposed != (previous.prerequisites if previous is not None else original)
        or effective_mapping != original_mapping
        or effective_mapping != previous_mapping
        or citations != original_citations
        or citations != previous_citations
    ):
        raise PresalesError("presales_review_note_required")
    try:
        ModelDraft(
            **payload.model_dump(
                exclude={"expected_revision", "note", "prerequisite_changes", "citations"}
            ),
            citations=citations,
        )
    except ValidationError as error:
        raise PresalesError("presales_review_evidence_required") from error
