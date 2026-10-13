from dataclasses import replace
from uuid import UUID

import pytest

from enterprise_doc_core.documents.retrieval import RetrievalCandidate
from enterprise_doc_core.presales.context_window import (
    WindowChunk,
    context_candidates,
    select_context,
    select_ranked_context,
)
from enterprise_doc_core.presales.errors import PresalesError


def chunk(index, text=None):
    text = text if text is not None else str(index) * 1200
    return WindowChunk(
        RetrievalCandidate(
            chunk_id=UUID(int=index + 1),
            tenant_id=UUID(int=100),
            document_version_id=UUID(int=200),
            generation_id=UUID(int=300),
            text=text,
            start_offset=index * 1200,
            end_offset=index * 1200 + len(text),
            source_filename="original.txt",
        ),
        index,
    )


def test_actual_haystack_adds_complete_neighbor_without_displacing_originals(monkeypatch):
    import socket

    def no_network(*_args, **_kwargs):
        raise AssertionError("context component must be offline")

    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    pool = tuple(chunk(i) for i in range(5))
    seeds = (pool[0].candidate, pool[2].candidate)
    result = select_context(seeds, pool, extra_slots=10)
    assert result.candidates == (*seeds, pool[1].candidate)
    assert sum(len(c.text) for c in result.candidates) == 3600
    assert result.truncated
    assert result.candidates[-1].chunk_id == pool[1].candidate.chunk_id
    assert result.candidates[-1].start_offset == 1200
    from haystack.telemetry import _telemetry

    assert _telemetry.telemetry is None


@pytest.mark.parametrize("slots", [0, 1])
def test_context_budget_never_truncates_or_replaces_seeds(slots):
    pool = (chunk(0, "a" * 1800), chunk(1, "b" * 1200), chunk(2, "c" * 1800))
    seeds = (pool[0].candidate, pool[2].candidate)
    result = select_context(seeds, pool, extra_slots=slots)
    assert result.candidates == seeds
    assert result.truncated


def test_no_room_for_whole_neighbor_does_not_send_partial_clause():
    pool = (chunk(0, "a" * 1600), chunk(1, "b" * 1200), chunk(2, "c" * 1600))
    seeds = (pool[0].candidate, pool[2].candidate)
    assert select_context(seeds, pool, extra_slots=1).candidates == seeds


@pytest.mark.parametrize("field", ["tenant_id", "document_version_id", "generation_id"])
def test_foreign_context_is_rejected(field):
    seed = chunk(0)
    neighbor = chunk(1)
    foreign = replace(neighbor, candidate=replace(neighbor.candidate, **{field: UUID(int=999)}))
    with pytest.raises(PresalesError, match="presales_invalid_evidence"):
        select_context((seed.candidate,), (seed, foreign), extra_slots=1)


def test_missing_or_modified_seed_cannot_be_projected_as_original_chunk():
    seed, neighbor = chunk(0), chunk(1)
    for pool in [(neighbor,), (replace(seed, candidate=replace(seed.candidate, text="forged")),)]:
        with pytest.raises(PresalesError, match="presales_invalid_evidence"):
            select_context((seed.candidate,), pool, extra_slots=1)


def test_item_budget_radius_and_duplicate_windows_are_bounded():
    pool = tuple(chunk(i, str(i) * 50) for i in range(8))
    seeds = (pool[0].candidate, pool[2].candidate)
    result = select_context(seeds, pool, extra_slots=2)
    assert result.candidates == (*seeds, pool[1].candidate, pool[3].candidate)
    assert len(set(c.chunk_id for c in result.candidates)) == 4
    assert all(c.chunk_id != pool[7].candidate.chunk_id for c in result.candidates)


def test_empty_seed_does_not_invent_evidence():
    assert select_context((), (), extra_slots=3).candidates == ()


@pytest.mark.parametrize("slots", [-1, 12])
def test_invalid_item_budget_rejects_before_loading_component(slots):
    seed = chunk(0)
    with pytest.raises(PresalesError, match="presales_invalid_evidence"):
        select_context((seed.candidate,), (seed,), extra_slots=slots)


def windows(count=5):
    result = []
    for source in range(count):
        pool = tuple(
            replace(
                chunk(index),
                candidate=replace(
                    chunk(index).candidate,
                    chunk_id=UUID(int=1000 + source * 10 + index),
                    document_version_id=UUID(int=200 + source),
                    generation_id=UUID(int=300 + source),
                ),
            )
            for index in range(4)
        )
        result.append(((pool[0].candidate, pool[2].candidate), pool))
    return tuple(result)


def score_map(groups):
    return {c.chunk_id: 0.0 for seeds, pool in groups for c in context_candidates(seeds, pool)}


def test_later_source_and_farther_neighbor_compete_for_remaining_slots():
    groups = windows()
    scores = score_map(groups)
    scores[groups[4][1][3].candidate.chunk_id] = 0.9
    scores[groups[3][1][1].candidate.chunk_id] = 0.8
    selected = select_ranked_context(groups, scores)
    assert selected[4].candidates == (*groups[4][0], groups[4][1][3].candidate)
    assert selected[3].candidates == (*groups[3][0], groups[3][1][1].candidate)
    assert all(selected[i].candidates == groups[i][0] for i in range(3))
    assert sum(len(w.candidates) for w in selected) == 12
    assert all(sum(len(c.text) for c in w.candidates) <= 3600 for w in selected)
    assert all(w.truncated for w in selected)


def test_reordering_sources_cannot_change_equal_score_selection():
    groups = windows()
    scores = score_map(groups)
    normal = select_ranked_context(groups, scores)
    reversed_result = select_ranked_context(tuple(reversed(groups)), scores)
    assert {c.chunk_id for w in normal for c in w.candidates} == {
        c.chunk_id for w in reversed_result for c in w.candidates
    }


@pytest.mark.parametrize("fault", ["missing", "foreign", "nan", "infinite", "boolean"])
def test_ranker_cannot_inject_or_omit_candidates_or_invalid_scores(fault):
    groups = windows(1)
    scores = score_map(groups)
    key = next(iter(scores))
    if fault == "missing":
        del scores[key]
    elif fault == "foreign":
        scores[UUID(int=99999)] = 1.0
    else:
        scores[key] = {"nan": float("nan"), "infinite": float("inf"), "boolean": True}[fault]
    with pytest.raises(PresalesError, match="presales_invalid_evidence"):
        select_ranked_context(groups, scores)


def test_six_sources_keep_all_seeds_and_no_neighbors():
    groups = windows(6)
    selected = select_ranked_context(groups, score_map(groups))
    assert [w.candidates for w in selected] == [seeds for seeds, _ in groups]
    assert select_ranked_context((), {}) == ()
    assert select_ranked_context((((), ()),), {})[0].candidates == ()


@pytest.mark.parametrize("fault", ["tenant", "duplicate_source", "too_many_sources"])
def test_global_ranker_validates_all_sources_before_admission(fault):
    groups = windows(7 if fault == "too_many_sources" else 2)
    if fault == "tenant":
        _seeds, pool = groups[1]
        pool = tuple(
            replace(c, candidate=replace(c.candidate, tenant_id=UUID(int=999))) for c in pool
        )
        groups = (groups[0], ((pool[0].candidate, pool[2].candidate), pool))
    elif fault == "duplicate_source":
        groups = (groups[0], groups[0])
    with pytest.raises(PresalesError, match="presales_invalid_evidence"):
        select_ranked_context(groups, score_map(groups))
