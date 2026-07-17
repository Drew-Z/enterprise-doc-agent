from __future__ import annotations

import warnings
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from enterprise_doc_core.config import UploadSettings
from enterprise_doc_core.documents.envelope import (
    DocumentEnvelopeViolation,
    validate_document_envelope,
)


class RangeObjectStore:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.calls: list[tuple[int, int]] = []

    async def get_range(
        self,
        *,
        bucket: str,
        key: str,
        start: int,
        end_inclusive: int,
    ) -> bytes:
        assert bucket == "documents"
        assert key == "m1/object"
        assert 0 <= start <= end_inclusive < len(self.content)
        self.calls.append((start, end_inclusive))
        return self.content[start : end_inclusive + 1]


async def _validate(
    content: bytes,
    *,
    extension: str,
    settings: UploadSettings | None = None,
) -> tuple[str, RangeObjectStore]:
    store = RangeObjectStore(content)
    result = await validate_document_envelope(
        object_store=store,
        bucket="documents",
        key="m1/object",
        size_bytes=len(content),
        extension=extension,
        settings=settings if settings is not None else UploadSettings(),
    )
    return result.detected_media_type, store


async def test_pdf_envelope_requires_a_leading_signature_and_reads_five_bytes() -> None:
    detected_media_type, store = await _validate(b"%PDF-1.7\nbody", extension=".pdf")

    assert detected_media_type == "application/pdf"
    assert store.calls == [(0, 4)]


@pytest.mark.parametrize("content", [b"%PDF", b" %PDF-", b"%pdf-", b"\xef\xbb\xbf%PDF-"])
async def test_pdf_envelope_rejects_invalid_signatures(content: bytes) -> None:
    with pytest.raises(DocumentEnvelopeViolation) as exc_info:
        await _validate(content, extension=".pdf")

    assert exc_info.value.code == "document_pdf_signature_invalid"


async def test_txt_envelope_reads_bounded_head_and_tail_samples() -> None:
    content = "alpha-\u4e2d-middle-omega".encode()
    settings = UploadSettings(envelope_sample_bytes=8)

    detected_media_type, store = await _validate(
        content,
        extension=".txt",
        settings=settings,
    )

    assert detected_media_type == "text/plain"
    assert store.calls == [(0, 7), (len(content) - 8, len(content) - 1)]
    assert sum(end - start + 1 for start, end in store.calls) == 16


@pytest.mark.parametrize(
    ("content", "expected_code"),
    [
        (b"nul\x00prefix" + b"x" * 20, "document_txt_nul"),
        (b"x" * 20 + b"suffix\x00", "document_txt_nul"),
        (b"bad-\xff-prefix" + b"x" * 20, "document_txt_invalid_utf8"),
        (b"x" * 20 + b"bad-\xff-tail", "document_txt_invalid_utf8"),
    ],
)
async def test_txt_envelope_rejects_sampled_nul_and_invalid_utf8(
    content: bytes,
    expected_code: str,
) -> None:
    with pytest.raises(DocumentEnvelopeViolation) as exc_info:
        await _validate(
            content,
            extension=".txt",
            settings=UploadSettings(envelope_sample_bytes=12),
        )

    assert exc_info.value.code == expected_code


async def test_txt_envelope_does_not_read_an_unsampled_middle() -> None:
    content = b"valid-head" + b"\x00\xff" + b"valid-tail"

    detected_media_type, store = await _validate(
        content,
        extension=".txt",
        settings=UploadSettings(envelope_sample_bytes=8),
    )

    assert detected_media_type == "text/plain"
    assert store.calls == [(0, 7), (len(content) - 8, len(content) - 1)]


def _docx(entries: list[tuple[str, bytes]]) -> bytes:
    output = BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
            for name, content in entries:
                archive.writestr(name, content)
    return output.getvalue()


def _valid_docx_entries() -> list[tuple[str, bytes]]:
    return [
        ("[Content_Types].xml", b"<Types/>"),
        ("word/document.xml", b"<document/>"),
        ("_rels/.rels", b"<Relationships/>"),
    ]


def _set_first_central_flags(content: bytes, flags: int) -> bytes:
    mutated = bytearray(content)
    offset = mutated.index(b"PK\x01\x02")
    mutated[offset + 8 : offset + 10] = flags.to_bytes(2, "little")
    return bytes(mutated)


def _set_little_endian(content: bytes, *, offset: int, width: int, value: int) -> bytes:
    mutated = bytearray(content)
    mutated[offset : offset + width] = value.to_bytes(width, "little")
    return bytes(mutated)


def _with_first_central_extra(content: bytes, extra: bytes) -> bytes:
    mutated = bytearray(content)
    central_offset = mutated.index(b"PK\x01\x02")
    name_length = int.from_bytes(mutated[central_offset + 28 : central_offset + 30], "little")
    extra_length = int.from_bytes(mutated[central_offset + 30 : central_offset + 32], "little")
    insert_at = central_offset + 46 + name_length + extra_length
    mutated[insert_at:insert_at] = extra
    mutated[central_offset + 30 : central_offset + 32] = (extra_length + len(extra)).to_bytes(
        2, "little"
    )
    eocd_offset = mutated.rindex(b"PK\x05\x06")
    central_size = int.from_bytes(mutated[eocd_offset + 12 : eocd_offset + 16], "little")
    mutated[eocd_offset + 12 : eocd_offset + 16] = (central_size + len(extra)).to_bytes(4, "little")
    return bytes(mutated)


async def test_docx_envelope_reads_only_tail_and_bounded_central_directory() -> None:
    content = _docx(_valid_docx_entries())
    settings = UploadSettings(max_docx_central_directory_bytes=4096)

    detected_media_type, store = await _validate(
        content,
        extension=".docx",
        settings=settings,
    )

    assert detected_media_type == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert len(store.calls) == 2
    assert store.calls[0][1] == len(content) - 1
    assert sum(end - start + 1 for start, end in store.calls) <= len(content) + 4096


async def test_docx_envelope_rejects_an_oversized_central_directory_before_reading_it() -> None:
    content = _docx(_valid_docx_entries())
    store = RangeObjectStore(content)

    with pytest.raises(DocumentEnvelopeViolation) as exc_info:
        await validate_document_envelope(
            object_store=store,
            bucket="documents",
            key="m1/object",
            size_bytes=len(content),
            extension=".docx",
            settings=UploadSettings(max_docx_central_directory_bytes=46),
        )

    assert exc_info.value.code == "document_docx_zip_invalid"
    assert len(store.calls) == 1


@pytest.mark.parametrize(
    ("entries", "expected_code"),
    [
        (
            [("[Content_Types].xml", b"<Types/>")],
            "document_docx_required_entry_missing",
        ),
        (
            [*_valid_docx_entries(), ("../escape.xml", b"bad")],
            "document_docx_path_unsafe",
        ),
        (
            [*_valid_docx_entries(), ("C:settings.xml", b"bad")],
            "document_docx_path_unsafe",
        ),
        (
            [*_valid_docx_entries(), ("word/document.xml", b"duplicate")],
            "document_docx_duplicate_entry",
        ),
    ],
)
async def test_docx_envelope_rejects_missing_unsafe_and_duplicate_entries(
    entries: list[tuple[str, bytes]],
    expected_code: str,
) -> None:
    with pytest.raises(DocumentEnvelopeViolation) as exc_info:
        await _validate(_docx(entries), extension=".docx")

    assert exc_info.value.code == expected_code


async def test_docx_envelope_rejects_encrypted_flags() -> None:
    content = _set_first_central_flags(_docx(_valid_docx_entries()), 1)

    with pytest.raises(DocumentEnvelopeViolation) as exc_info:
        await _validate(content, extension=".docx")

    assert exc_info.value.code == "document_docx_encrypted"


@pytest.mark.parametrize(
    "mutation",
    ["multi-disk", "zip64-eocd", "zip64-local-offset", "zip64-extra"],
)
async def test_docx_envelope_rejects_multi_disk_and_zip64_records(mutation: str) -> None:
    content = _docx(_valid_docx_entries())
    central_offset = content.index(b"PK\x01\x02")
    eocd_offset = content.rindex(b"PK\x05\x06")
    if mutation == "multi-disk":
        content = _set_little_endian(content, offset=eocd_offset + 4, width=2, value=1)
    elif mutation == "zip64-eocd":
        content = _set_little_endian(
            content,
            offset=eocd_offset + 8,
            width=2,
            value=0xFFFF,
        )
        content = _set_little_endian(
            content,
            offset=eocd_offset + 10,
            width=2,
            value=0xFFFF,
        )
    elif mutation == "zip64-local-offset":
        content = _set_little_endian(
            content,
            offset=central_offset + 42,
            width=4,
            value=0xFFFFFFFF,
        )
    else:
        content = _with_first_central_extra(content, b"\x01\x00\x00\x00")

    with pytest.raises(DocumentEnvelopeViolation) as exc_info:
        await _validate(content, extension=".docx")

    assert exc_info.value.code == "document_docx_zip_invalid"


@pytest.mark.parametrize("mutation", ["signature", "name-length", "entry-count", "method"])
async def test_docx_envelope_rejects_malformed_central_records(mutation: str) -> None:
    content = _docx(_valid_docx_entries())
    central_offset = content.index(b"PK\x01\x02")
    eocd_offset = content.rindex(b"PK\x05\x06")
    if mutation == "signature":
        mutated = bytearray(content)
        mutated[central_offset : central_offset + 4] = b"BAD!"
        content = bytes(mutated)
    elif mutation == "name-length":
        content = _set_little_endian(
            content,
            offset=central_offset + 28,
            width=2,
            value=0xFFFF,
        )
    elif mutation == "entry-count":
        entry_count = int.from_bytes(content[eocd_offset + 10 : eocd_offset + 12], "little")
        content = _set_little_endian(
            content,
            offset=eocd_offset + 8,
            width=2,
            value=entry_count + 1,
        )
        content = _set_little_endian(
            content,
            offset=eocd_offset + 10,
            width=2,
            value=entry_count + 1,
        )
    else:
        content = _set_little_endian(
            content,
            offset=central_offset + 10,
            width=2,
            value=99,
        )

    with pytest.raises(DocumentEnvelopeViolation) as exc_info:
        await _validate(content, extension=".docx")

    assert exc_info.value.code == "document_docx_zip_invalid"


async def test_docx_envelope_rejects_names_duplicated_after_nfc_normalization() -> None:
    entries = [
        *_valid_docx_entries(),
        ("word/caf\u00e9.xml", b"composed"),
        ("word/cafe\u0301.xml", b"decomposed"),
    ]

    with pytest.raises(DocumentEnvelopeViolation) as exc_info:
        await _validate(_docx(entries), extension=".docx")

    assert exc_info.value.code == "document_docx_duplicate_entry"


@pytest.mark.parametrize(
    ("settings", "expected_code"),
    [
        (UploadSettings(max_docx_entries=2), "document_docx_entry_limit_exceeded"),
        (
            UploadSettings(max_docx_declared_uncompressed_bytes=20),
            "document_docx_declared_size_exceeded",
        ),
        (
            UploadSettings(max_docx_member_uncompressed_bytes=10),
            "document_docx_member_size_exceeded",
        ),
        (
            UploadSettings(max_docx_member_compression_ratio=1.1),
            "document_docx_compression_ratio_exceeded",
        ),
    ],
)
async def test_docx_envelope_enforces_declared_metadata_limits(
    settings: UploadSettings,
    expected_code: str,
) -> None:
    entries = [*_valid_docx_entries(), ("word/large.xml", b"A" * 128)]

    with pytest.raises(DocumentEnvelopeViolation) as exc_info:
        await _validate(
            _docx(entries),
            extension=".docx",
            settings=settings,
        )

    assert exc_info.value.code == expected_code
