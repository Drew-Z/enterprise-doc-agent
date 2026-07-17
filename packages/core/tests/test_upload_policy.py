from __future__ import annotations

import unicodedata
from uuid import uuid4

import pytest

from enterprise_doc_core.config import UploadSettings
from enterprise_doc_core.uploads.policy import (
    UploadPolicyViolation,
    build_object_key,
    plan_multipart_upload,
    validate_upload_metadata,
)

MIB = 1024**2
GIB = 1024**3
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.mark.parametrize(
    ("filename", "media_type", "extension"),
    [
        ("notes.txt", "text/plain", ".txt"),
        ("report.PDF", "application/pdf", ".pdf"),
        ("contract.docx", DOCX_MEDIA_TYPE, ".docx"),
    ],
)
def test_valid_upload_metadata_is_normalized_and_planned(
    filename: str,
    media_type: str,
    extension: str,
) -> None:
    result = validate_upload_metadata(
        filename=filename,
        size_bytes=17 * MIB,
        media_type=media_type,
        sha256="a" * 64,
        settings=UploadSettings(),
    )

    assert result.filename == filename
    assert result.extension == extension
    assert result.media_type == media_type
    assert result.part_size_bytes >= 5 * MIB
    assert result.part_count == 2
    assert len(result.request_fingerprint) == 64


@pytest.mark.parametrize(
    ("filename", "size_bytes", "media_type", "sha256", "expected_code"),
    [
        ("../report.pdf", MIB, "application/pdf", "a" * 64, "upload_filename_unsafe"),
        (r"C:\report.pdf", MIB, "application/pdf", "a" * 64, "upload_filename_unsafe"),
        ("bad\x00name.txt", MIB, "text/plain", "a" * 64, "upload_filename_unsafe"),
        ("safe\u202ename.txt", MIB, "text/plain", "a" * 64, "upload_filename_unsafe"),
        ("safe\u200b.txt", MIB, "text/plain", "a" * 64, "upload_filename_unsafe"),
        ("bad\ud800.txt", MIB, "text/plain", "a" * 64, "upload_filename_unsafe"),
        (".", MIB, "text/plain", "a" * 64, "upload_filename_unsafe"),
        ("CON.txt", MIB, "text/plain", "a" * 64, "upload_filename_unsafe"),
        ("COM\u00b9.txt", MIB, "text/plain", "a" * 64, "upload_filename_unsafe"),
        ("LPT\u00b2.pdf", MIB, "application/pdf", "a" * 64, "upload_filename_unsafe"),
        ("report.pdf. ", MIB, "application/pdf", "a" * 64, "upload_filename_unsafe"),
        ("archive.zip", MIB, "application/zip", "a" * 64, "upload_type_unsupported"),
        ("report.pdf", MIB, "text/plain", "a" * 64, "upload_media_type_mismatch"),
        ("empty.txt", 0, "text/plain", "a" * 64, "upload_size_invalid"),
        ("large.txt", 11 * GIB, "text/plain", "a" * 64, "upload_size_exceeded"),
        ("hash.txt", MIB, "text/plain", "A" * 64, "upload_sha256_invalid"),
        ("hash.txt", MIB, "text/plain", "abc", "upload_sha256_invalid"),
    ],
)
def test_invalid_upload_metadata_returns_a_stable_policy_code(
    filename: str,
    size_bytes: int,
    media_type: str,
    sha256: str,
    expected_code: str,
) -> None:
    with pytest.raises(UploadPolicyViolation) as exc_info:
        validate_upload_metadata(
            filename=filename,
            size_bytes=size_bytes,
            media_type=media_type,
            sha256=sha256,
            settings=UploadSettings(),
        )

    assert exc_info.value.code == expected_code


def test_filename_length_limit_is_configurable() -> None:
    settings = UploadSettings(max_filename_length=20)

    with pytest.raises(UploadPolicyViolation, match="filename") as exc_info:
        validate_upload_metadata(
            filename="a" * 17 + ".txt",
            size_bytes=MIB,
            media_type="text/plain",
            sha256="a" * 64,
            settings=settings,
        )

    assert exc_info.value.code == "upload_filename_unsafe"


def test_part_planning_stays_within_the_s3_ten_thousand_part_limit() -> None:
    boundary = plan_multipart_upload(
        size_bytes=5 * MIB * 10_000,
        preferred_part_size_bytes=5 * MIB,
    )
    above_boundary = plan_multipart_upload(
        size_bytes=5 * MIB * 10_000 + 1,
        preferred_part_size_bytes=5 * MIB,
    )

    assert boundary.part_count == 10_000
    assert boundary.part_size_bytes == 5 * MIB
    assert above_boundary.part_count <= 10_000
    assert above_boundary.part_size_bytes > 5 * MIB

    with pytest.raises(UploadPolicyViolation) as exc_info:
        plan_multipart_upload(
            size_bytes=5 * 1024**4 + 1,
            preferred_part_size_bytes=5 * MIB,
        )
    assert exc_info.value.code == "upload_part_count_invalid"


@pytest.mark.parametrize("preferred_part_size_bytes", [5 * MIB - 1, 5 * GIB + 1])
def test_part_planning_rejects_part_sizes_outside_s3_bounds(
    preferred_part_size_bytes: int,
) -> None:
    with pytest.raises(UploadPolicyViolation) as exc_info:
        plan_multipart_upload(
            size_bytes=GIB,
            preferred_part_size_bytes=preferred_part_size_bytes,
        )

    assert exc_info.value.code == "upload_part_size_invalid"


def test_request_fingerprint_uses_canonical_unicode_and_media_type() -> None:
    composed = "resume-\u00e9.txt"
    decomposed = unicodedata.normalize("NFD", composed)

    first = validate_upload_metadata(
        filename=composed,
        size_bytes=MIB,
        media_type="TEXT/PLAIN",
        sha256="b" * 64,
        settings=UploadSettings(),
    )
    second = validate_upload_metadata(
        filename=decomposed,
        size_bytes=MIB,
        media_type=" text/plain ",
        sha256="b" * 64,
        settings=UploadSettings(),
    )

    assert first.filename == second.filename
    assert first.request_fingerprint == second.request_fingerprint


def test_random_object_key_contains_only_server_owned_identifiers() -> None:
    session_id = uuid4()
    version_id = uuid4()
    object_key = build_object_key(session_id=session_id, version_id=version_id)

    assert session_id.hex in object_key
    assert version_id.hex in object_key
    assert "contract.pdf" not in object_key
    assert "tenant" not in object_key
    assert "@" not in object_key
    assert "\\" not in object_key
