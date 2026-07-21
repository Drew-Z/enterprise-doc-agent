from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.validate_buildkit_provenance import (
    BuildKitProvenanceError,
    validate_buildkit_provenance,
)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_accepts_slsa_v02_predicate(tmp_path: Path) -> None:
    source = tmp_path / "provenance.json"
    _write_json(source, {"buildType": "https://mobyproject.org/buildkit@v1"})

    assert validate_buildkit_provenance(source) == "slsaprovenance02"


def test_accepts_slsa_v1_predicate_returned_by_current_buildkit(tmp_path: Path) -> None:
    source = tmp_path / "provenance.json"
    _write_json(
        source,
        {
            "buildDefinition": {
                "buildType": "https://github.com/moby/buildkit/slsa-definitions.md"
            },
            "runDetails": {"builder": {"id": "https://github.com/example/actions/runs/42"}},
        },
    )

    assert validate_buildkit_provenance(source) == "slsaprovenance1"


@pytest.mark.parametrize(
    "payload",
    (
        {},
        {"buildType": ""},
        {"buildDefinition": {"buildType": "https://example/build"}},
        {"buildDefinition": {}, "runDetails": {}},
        {
            "buildType": "https://example/v02",
            "buildDefinition": {"buildType": "https://example/v1"},
            "runDetails": {},
        },
    ),
)
def test_rejects_unknown_incomplete_or_ambiguous_predicate(tmp_path: Path, payload: object) -> None:
    source = tmp_path / "provenance.json"
    _write_json(source, payload)

    with pytest.raises(BuildKitProvenanceError):
        validate_buildkit_provenance(source)
