from __future__ import annotations

import hashlib
import json

import pytest
from scripts.backup_retention import plan_retention


def write(path, value):
    data = value if isinstance(value, bytes) else json.dumps(value).encode()
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def fixture_group(tmp_path, count=7):
    stage = tmp_path / "final-delivery-evidence"
    stage.mkdir()
    records = {}
    for i in range(count):
        name = f"backup-tick-20260929-120{i:03d}-000000"
        directory = stage / name
        directory.mkdir()
        sha = write(directory / "database.dump", str(i).encode())
        object_sha = write(directory / "objects.private.json", {"objects": []})
        write(directory / "receipt.json", {"status": "passed", "database_sha256": sha})
        write(directory / "cleanup.json", {"removed_owned_container": str(i)})
        records[name] = {
            "status": "verified",
            "pinned": False,
            "created_at": f"2026-09-29T12:0{i}:00+00:00",
            "database_path": str(directory / "database.dump"),
            "database_sha256": sha,
            "objects_manifest_path": str(directory / "objects.private.json"),
            "objects_manifest_sha256": object_sha,
        }
    write(
        tmp_path / "manifest.json", {"final_delivery_phase": {"scheduled_backup_records": records}}
    )
    write(tmp_path / "final-delivery-artifact-index.json", {"artifacts": []})
    return stage, records


def test_only_old_verified_unreferenced_snapshots_are_retirement_candidates(tmp_path):
    stage, records = fixture_group(tmp_path)
    plan = plan_retention(tmp_path)
    assert [p["directory"] for p in plan["candidates"]] == [
        str(stage / name) for name in list(records)[:2]
    ]
    assert all((stage / name / "database.dump").exists() for name in records)
    assert len(plan["retained"]) == 5


def test_frozen_pinned_and_object_referenced_snapshots_are_preserved(tmp_path):
    stage, records = fixture_group(tmp_path, 9)
    names = list(records)
    write(
        tmp_path / "final-delivery-artifact-index.json",
        {"artifacts": [{"path": str(stage / names[0] / "database.dump")}]},
    )
    records[names[1]]["pinned"] = True
    records[names[-1]]["objects_manifest_sha256"] = write(
        stage / names[-1] / "objects.private.json",
        {"objects": [{"backup_path": str(stage / names[2] / "database.dump")}]},
    )
    write(
        tmp_path / "manifest.json", {"final_delivery_phase": {"scheduled_backup_records": records}}
    )
    plan = plan_retention(tmp_path)
    assert [v["name"] for v in plan["candidates"]] == [names[3]]
    assert {v["reason"] for v in plan["retained"]} >= {
        "frozen_evidence",
        "pinned_or_unverified",
        "referenced_object_bytes",
    }


@pytest.mark.parametrize("change", ["database", "object_manifest", "unknown_file", "path_escape"])
def test_changed_or_unowned_contents_stop_retirement(tmp_path, change):
    stage, records = fixture_group(tmp_path)
    name = next(iter(records))
    if change == "database":
        (stage / name / "database.dump").write_bytes(b"changed")
    elif change == "object_manifest":
        (stage / name / "objects.private.json").write_text("{}")
    elif change == "unknown_file":
        (stage / name / "user-data.txt").write_bytes(b"keep")
    else:
        records[name]["database_path"] = str(tmp_path / "database.dump")
        write(
            tmp_path / "manifest.json",
            {"final_delivery_phase": {"scheduled_backup_records": records}},
        )
    with pytest.raises(ValueError):
        plan_retention(tmp_path)
    assert (stage / name / "database.dump").exists()


def test_missing_cleanup_and_unknown_backup_directories_are_never_candidates(tmp_path):
    stage, records = fixture_group(tmp_path)
    name = next(iter(records))
    (stage / name / "cleanup.json").unlink()
    other = stage / "unowned-backup"
    other.mkdir()
    (other / "database.dump").write_bytes(b"unowned")
    plan = plan_retention(tmp_path)
    assert [v["name"] for v in plan["candidates"]] == [list(records)[1]]
    assert (other / "database.dump").exists()


def test_retired_records_allow_a_second_audit_without_reading_deleted_files(tmp_path):
    stage, records = fixture_group(tmp_path)
    name = next(iter(records))
    records[name]["status"] = "retired"
    for path in (stage / name).iterdir():
        path.unlink()
    (stage / name).rmdir()
    write(
        tmp_path / "manifest.json", {"final_delivery_phase": {"scheduled_backup_records": records}}
    )
    assert [v["name"] for v in plan_retention(tmp_path)["candidates"]] == [list(records)[1]]


def test_corrupt_recent_snapshot_cannot_displace_an_older_recovery_point(tmp_path):
    stage, records = fixture_group(tmp_path)
    (stage / list(records)[-1] / "database.dump").write_bytes(b"damaged")
    with pytest.raises(ValueError, match="database_changed"):
        plan_retention(tmp_path)
