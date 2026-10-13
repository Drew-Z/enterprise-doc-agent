from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MATERIALS = ROOT / ".trellis/tasks/10-11-document-analysis-workspace/materials"


@pytest.mark.parametrize(
    ("pack_name", "expected_split"),
    (("validation-01", "validation"), ("holdout-football-01", "holdout")),
)
def test_a5_pack_keeps_sources_reference_and_denominator_frozen(
    pack_name: str, expected_split: str
) -> None:
    root = MATERIALS / pack_name
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    reference = json.loads((root / manifest["reference"]).read_text(encoding="utf-8"))

    assert manifest["schema_version"] == "analysis.public-pack.v2"
    assert manifest["split"] == expected_split
    assert manifest["independent_customer"] is False
    assert manifest["independently_scored"] is False
    assert manifest["quality_score"] is None
    assert len(manifest["objects"]) == 3
    assert len(manifest["fields"]) == 10
    assert len(manifest["sources"]) == 6

    expected_uploads = {"requirements.synthetic.txt"}
    object_ids = {item["id"] for item in manifest["objects"]}
    field_ids = {item["id"] for item in manifest["fields"]}
    for source in manifest["sources"]:
        relative = Path(source["file"])
        assert relative.parts[0] == "upload"
        expected_uploads.add(relative.name)
        assert source["object_id"] in object_ids
        assert source["url"].startswith("https://")
        assert source["coverage"] == "unknown"
        path = root / relative
        assert path.stat().st_size == source["bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == source["sha256"]
        raw = Path(source["raw_evidence"])
        assert raw.is_file()
        assert hashlib.sha256(raw.read_bytes()).hexdigest() == source["raw_sha256"]

    requirement = root / manifest["common_requirements"]["file"]
    assert requirement.stat().st_size == manifest["common_requirements"]["bytes"]
    assert (
        hashlib.sha256(requirement.read_bytes()).hexdigest()
        == manifest["common_requirements"]["sha256"]
    )
    assert {path.name for path in (root / "upload").iterdir()} == expected_uploads
    assert not any("reference" in path.name.lower() for path in (root / "upload").iterdir())

    expected_rows = {(object_id, field_id) for object_id in object_ids for field_id in field_ids}
    actual_rows = {(row["object_id"], row["field_id"]) for row in reference["rows"]}
    assert actual_rows == expected_rows
    assert reference["denominator"] == 30
    assert reference["annotated_rows"] == 0
    assert reference["unreviewed_rows"] == 30
    assert reference["quality_score"] is None
    assert reference["independently_reviewed"] is False


def test_football_holdout_preserves_discovered_source_conflict() -> None:
    manifest = json.loads(
        (MATERIALS / "holdout-football-01/manifest.json").read_text(encoding="utf-8")
    )
    assert any("60-day PUMA" in gap and "30 days" in gap for gap in manifest["known_gaps"])
