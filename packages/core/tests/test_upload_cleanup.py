from __future__ import annotations

from uuid import UUID

import pytest

from enterprise_doc_core.uploads import UploadCleanupReport, parse_upload_object_key


def test_parse_upload_object_key_accepts_only_the_exact_m1_random_shape() -> None:
    session_id = UUID("12345678-1234-5678-1234-567812345678")
    version_id = UUID("abcdefab-cdef-abcd-efab-cdefabcdefab")

    parsed = parse_upload_object_key(f"m1/uploads/{session_id.hex}/{version_id.hex}")

    assert parsed is not None
    assert parsed.session_id == session_id
    assert parsed.version_id == version_id


@pytest.mark.parametrize(
    "key",
    [
        "m1/uploads/12345678123456781234567812345678",
        "m1/uploads/12345678123456781234567812345678/abcdefabcdefabcdefabcdefabcdefab/extra",
        "m1/uploads/12345678-1234-5678-1234-567812345678/abcdefabcdefabcdefabcdefabcdefab",
        "m1/uploads/12345678123456781234567812345678/ABCDEFABCDEFABCDEFABCDEFABCDEFAB",
        "m1/uploads/1234567812345678123456781234567g/abcdefabcdefabcdefabcdefabcdefab",
        "m1/uploads/../abcdefabcdefabcdefabcdefabcdefab",
        "m1-probe/12345678123456781234567812345678/abcdefabcdefabcdefabcdefabcdefab",
    ],
)
def test_parse_upload_object_key_rejects_ambiguous_shapes(key: str) -> None:
    assert parse_upload_object_key(key) is None


def test_cleanup_report_exposes_only_error_class_counts() -> None:
    report = UploadCleanupReport.empty(dry_run=True).with_exception(
        RuntimeError("m1/uploads/private/upload-id")
    )

    payload = report.to_dict()

    assert payload["status"] == "failed"
    assert payload["dryRun"] is True
    assert payload["exceptionsByClass"] == {"RuntimeError": 1}
    assert "private" not in str(payload)
