import hashlib
import json
from pathlib import Path

from enterprise_doc_core.analysis.schemas import default_fields
from enterprise_doc_core.documents.structure import parse_document_structure


def test_public_pack_hashes_parse_and_reference_locations_are_reproducible():
    root = Path(__file__).resolve().parents[2]
    pack = root / ".trellis/tasks/10-11-document-analysis-workspace/materials/development-01"
    manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["split"] == "development" and not manifest["independent_customer"]
    for source in manifest["sources"]:
        data = (pack / source["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == source["sha256"]
        parsed = parse_document_structure(data, extension=".txt")
        assert parsed.blocks
        assert all(parsed.text[b.start_offset : b.end_offset] == b.text for b in parsed.blocks)
    reference = json.loads((pack / "reference.json").read_text(encoding="utf-8"))
    assert reference["denominator"] == len(reference["rows"]) == 30
    assert reference["quality_score"] is None
    assert {(r["object_id"], r["field_id"]) for r in reference["rows"]} == {
        (o, f.id) for o in ("asana", "trello", "basecamp") for f in default_fields()
    }
    for row in reference["rows"]:
        if row["locator"]:
            locator = row["locator"]
            lines = (pack / locator["file"]).read_text(encoding="utf-8").splitlines()
            assert locator["anchor"] in lines[locator["line"] - 1]
