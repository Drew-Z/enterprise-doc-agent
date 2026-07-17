from __future__ import annotations

import base64

import pytest

from enterprise_doc_core.uploads import (
    UploadPartChecksumInvalid,
    UploadPartNumberInvalid,
    calculate_expected_part_size,
    validate_part_checksum_sha256,
)


def test_part_checksum_requires_canonical_base64_sha256() -> None:
    checksum = base64.b64encode(b"a" * 32).decode("ascii")

    assert validate_part_checksum_sha256(checksum) == checksum

    for invalid in (
        "not-base64",
        base64.b64encode(b"short").decode("ascii"),
        checksum.rstrip("="),
    ):
        with pytest.raises(UploadPartChecksumInvalid):
            validate_part_checksum_sha256(invalid)


def test_expected_part_size_handles_regular_and_final_parts() -> None:
    assert (
        calculate_expected_part_size(
            size_bytes=11,
            part_size_bytes=5,
            expected_part_count=3,
            part_number=1,
        )
        == 5
    )
    assert (
        calculate_expected_part_size(
            size_bytes=11,
            part_size_bytes=5,
            expected_part_count=3,
            part_number=3,
        )
        == 1
    )

    for part_number in (0, 4):
        with pytest.raises(UploadPartNumberInvalid):
            calculate_expected_part_size(
                size_bytes=11,
                part_size_bytes=5,
                expected_part_count=3,
                part_number=part_number,
            )
