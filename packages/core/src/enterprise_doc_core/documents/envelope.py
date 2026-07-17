from __future__ import annotations

import codecs
import re
import struct
import unicodedata
from dataclasses import dataclass

from enterprise_doc_core.config import UploadSettings
from enterprise_doc_core.object_store import MultipartObjectStore

_PDF_MEDIA_TYPE = "application/pdf"
_TXT_MEDIA_TYPE = "text/plain"
_DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_ZIP_EOCD_SIGNATURE = b"PK\x05\x06"
_ZIP_CENTRAL_SIGNATURE = b"PK\x01\x02"
_ZIP_EOCD = struct.Struct("<4s4H2LH")
_ZIP_CENTRAL_HEADER = struct.Struct("<4s6H3L5H2L")
_ZIP_EOCD_MAX_BYTES = _ZIP_EOCD.size + 65_535
_ZIP64_UINT16 = 0xFFFF
_ZIP64_UINT32 = 0xFFFFFFFF
_ZIP64_EXTRA_FIELD_ID = 0x0001
_WINDOWS_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
_DOCX_REQUIRED_ENTRIES = frozenset({"[Content_Types].xml", "word/document.xml"})

_MESSAGES = {
    "document_type_unsupported": "The uploaded document type is unsupported.",
    "document_pdf_signature_invalid": "The PDF signature is invalid.",
    "document_txt_nul": "The text document contains a NUL byte in a validation sample.",
    "document_txt_invalid_utf8": "The text document is not valid UTF-8 in a validation sample.",
    "document_docx_zip_invalid": "The DOCX ZIP envelope is invalid.",
    "document_docx_encrypted": "Encrypted DOCX entries are not supported.",
    "document_docx_path_unsafe": "The DOCX contains an unsafe member path.",
    "document_docx_duplicate_entry": "The DOCX contains a duplicate member.",
    "document_docx_entry_limit_exceeded": "The DOCX entry limit was exceeded.",
    "document_docx_declared_size_exceeded": (
        "The DOCX declared uncompressed size limit was exceeded."
    ),
    "document_docx_member_size_exceeded": (
        "A DOCX member declared an excessive uncompressed size."
    ),
    "document_docx_compression_ratio_exceeded": (
        "A DOCX member declared an excessive compression ratio."
    ),
    "document_docx_required_entry_missing": "The DOCX is missing a required Office entry.",
    "document_envelope_read_budget_exceeded": "The document envelope read budget was exceeded.",
}


class DocumentEnvelopeViolation(ValueError):
    def __init__(self, *, code: str) -> None:
        self.code = code
        self.message = _MESSAGES[code]
        super().__init__(self.message)


@dataclass(frozen=True, slots=True)
class ValidatedDocumentEnvelope:
    detected_media_type: str


@dataclass(slots=True)
class _RangeBudget:
    object_store: MultipartObjectStore
    bucket: str
    key: str
    remaining_bytes: int

    async def read(self, *, start: int, end_inclusive: int) -> bytes:
        requested_bytes = end_inclusive - start + 1
        if requested_bytes < 1 or requested_bytes > self.remaining_bytes:
            raise DocumentEnvelopeViolation(code="document_envelope_read_budget_exceeded")
        self.remaining_bytes -= requested_bytes
        return await self.object_store.get_range(
            bucket=self.bucket,
            key=self.key,
            start=start,
            end_inclusive=end_inclusive,
        )


async def validate_document_envelope(
    *,
    object_store: MultipartObjectStore,
    bucket: str,
    key: str,
    size_bytes: int,
    extension: str,
    settings: UploadSettings,
) -> ValidatedDocumentEnvelope:
    if size_bytes < 1:
        raise DocumentEnvelopeViolation(code="document_type_unsupported")
    if extension == ".pdf":
        await _validate_pdf(
            object_store=object_store,
            bucket=bucket,
            key=key,
            size_bytes=size_bytes,
        )
        return ValidatedDocumentEnvelope(detected_media_type=_PDF_MEDIA_TYPE)
    if extension == ".txt":
        await _validate_txt(
            object_store=object_store,
            bucket=bucket,
            key=key,
            size_bytes=size_bytes,
            sample_bytes=settings.envelope_sample_bytes,
        )
        return ValidatedDocumentEnvelope(detected_media_type=_TXT_MEDIA_TYPE)
    if extension == ".docx":
        await _validate_docx(
            object_store=object_store,
            bucket=bucket,
            key=key,
            size_bytes=size_bytes,
            settings=settings,
        )
        return ValidatedDocumentEnvelope(detected_media_type=_DOCX_MEDIA_TYPE)
    raise DocumentEnvelopeViolation(code="document_type_unsupported")


async def _validate_pdf(
    *,
    object_store: MultipartObjectStore,
    bucket: str,
    key: str,
    size_bytes: int,
) -> None:
    if size_bytes < 5:
        raise DocumentEnvelopeViolation(code="document_pdf_signature_invalid")
    signature = await object_store.get_range(
        bucket=bucket,
        key=key,
        start=0,
        end_inclusive=4,
    )
    if signature != b"%PDF-":
        raise DocumentEnvelopeViolation(code="document_pdf_signature_invalid")


async def _validate_txt(
    *,
    object_store: MultipartObjectStore,
    bucket: str,
    key: str,
    size_bytes: int,
    sample_bytes: int,
) -> None:
    if size_bytes <= sample_bytes:
        content = await object_store.get_range(
            bucket=bucket,
            key=key,
            start=0,
            end_inclusive=size_bytes - 1,
        )
        _reject_txt_nul(content)
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise DocumentEnvelopeViolation(code="document_txt_invalid_utf8") from error
        return

    head = await object_store.get_range(
        bucket=bucket,
        key=key,
        start=0,
        end_inclusive=sample_bytes - 1,
    )
    tail = await object_store.get_range(
        bucket=bucket,
        key=key,
        start=size_bytes - sample_bytes,
        end_inclusive=size_bytes - 1,
    )
    _reject_txt_nul(head)
    _reject_txt_nul(tail)
    try:
        decoder = codecs.getincrementaldecoder("utf-8")()
        decoder.decode(head, final=False)
        continuation_bytes = 0
        while continuation_bytes < len(tail) and 0x80 <= tail[continuation_bytes] <= 0xBF:
            continuation_bytes += 1
        if continuation_bytes > 3:
            raise UnicodeDecodeError("utf-8", tail, 0, continuation_bytes, "invalid boundary")
        tail[continuation_bytes:].decode("utf-8")
    except UnicodeDecodeError as error:
        raise DocumentEnvelopeViolation(code="document_txt_invalid_utf8") from error


def _reject_txt_nul(content: bytes) -> None:
    if b"\x00" in content:
        raise DocumentEnvelopeViolation(code="document_txt_nul")


async def _validate_docx(
    *,
    object_store: MultipartObjectStore,
    bucket: str,
    key: str,
    size_bytes: int,
    settings: UploadSettings,
) -> None:
    if size_bytes < _ZIP_EOCD.size:
        raise DocumentEnvelopeViolation(code="document_docx_zip_invalid")
    tail_bytes = min(size_bytes, _ZIP_EOCD_MAX_BYTES)
    budget = _RangeBudget(
        object_store=object_store,
        bucket=bucket,
        key=key,
        remaining_bytes=tail_bytes + settings.max_docx_central_directory_bytes,
    )
    tail_start = size_bytes - tail_bytes
    tail = await budget.read(start=tail_start, end_inclusive=size_bytes - 1)
    eocd_offset = _find_eocd(tail)
    (
        _,
        disk_number,
        central_disk,
        entries_on_disk,
        entry_count,
        central_size,
        central_offset,
        _,
    ) = _ZIP_EOCD.unpack_from(tail, eocd_offset)
    if disk_number != 0 or central_disk != 0 or entries_on_disk != entry_count:
        raise DocumentEnvelopeViolation(code="document_docx_zip_invalid")
    if (
        entry_count == _ZIP64_UINT16
        or central_size == _ZIP64_UINT32
        or central_offset == _ZIP64_UINT32
    ):
        raise DocumentEnvelopeViolation(code="document_docx_zip_invalid")
    if entry_count > settings.max_docx_entries:
        raise DocumentEnvelopeViolation(code="document_docx_entry_limit_exceeded")
    if central_size > settings.max_docx_central_directory_bytes:
        raise DocumentEnvelopeViolation(code="document_docx_zip_invalid")
    absolute_eocd_offset = tail_start + eocd_offset
    if central_offset + central_size != absolute_eocd_offset:
        raise DocumentEnvelopeViolation(code="document_docx_zip_invalid")
    if central_size < 1 or central_offset < 0 or central_offset >= size_bytes:
        raise DocumentEnvelopeViolation(code="document_docx_zip_invalid")
    central_directory = await budget.read(
        start=central_offset,
        end_inclusive=central_offset + central_size - 1,
    )
    _validate_central_directory(
        central_directory,
        central_directory_offset=central_offset,
        expected_entry_count=entry_count,
        settings=settings,
    )


def _find_eocd(tail: bytes) -> int:
    search_end = len(tail)
    while True:
        offset = tail.rfind(_ZIP_EOCD_SIGNATURE, 0, search_end)
        if offset < 0:
            raise DocumentEnvelopeViolation(code="document_docx_zip_invalid")
        if offset + _ZIP_EOCD.size <= len(tail):
            comment_length = int.from_bytes(tail[offset + 20 : offset + 22], "little")
            if offset + _ZIP_EOCD.size + comment_length == len(tail):
                return offset
        search_end = offset


def _validate_central_directory(
    content: bytes,
    *,
    central_directory_offset: int,
    expected_entry_count: int,
    settings: UploadSettings,
) -> None:
    offset = 0
    entry_count = 0
    total_uncompressed_bytes = 0
    seen_names: set[str] = set()
    present_files: set[str] = set()
    while offset < len(content):
        if len(content) - offset < _ZIP_CENTRAL_HEADER.size:
            raise DocumentEnvelopeViolation(code="document_docx_zip_invalid")
        fields = _ZIP_CENTRAL_HEADER.unpack_from(content, offset)
        if fields[0] != _ZIP_CENTRAL_SIGNATURE:
            raise DocumentEnvelopeViolation(code="document_docx_zip_invalid")
        flags = fields[3]
        compression_method = fields[4]
        compressed_size = fields[8]
        uncompressed_size = fields[9]
        name_length = fields[10]
        extra_length = fields[11]
        comment_length = fields[12]
        disk_start = fields[13]
        local_header_offset = fields[16]
        if (
            disk_start != 0
            or compressed_size == _ZIP64_UINT32
            or uncompressed_size == _ZIP64_UINT32
            or local_header_offset == _ZIP64_UINT32
            or local_header_offset >= central_directory_offset
        ):
            raise DocumentEnvelopeViolation(code="document_docx_zip_invalid")
        if flags & 0x0001 or flags & 0x0040:
            raise DocumentEnvelopeViolation(code="document_docx_encrypted")
        if compression_method not in {0, 8}:
            raise DocumentEnvelopeViolation(code="document_docx_zip_invalid")
        entry_end = offset + _ZIP_CENTRAL_HEADER.size + name_length + extra_length + comment_length
        if entry_end > len(content):
            raise DocumentEnvelopeViolation(code="document_docx_zip_invalid")
        name_start = offset + _ZIP_CENTRAL_HEADER.size
        name_bytes = content[name_start : name_start + name_length]
        extra_start = name_start + name_length
        extra_bytes = content[extra_start : extra_start + extra_length]
        _validate_zip_extra_fields(extra_bytes)
        try:
            name = name_bytes.decode("utf-8" if flags & 0x0800 else "cp437")
        except UnicodeDecodeError as error:
            raise DocumentEnvelopeViolation(code="document_docx_zip_invalid") from error
        normalized_name, is_directory = _validate_docx_name(name)
        if normalized_name in seen_names:
            raise DocumentEnvelopeViolation(code="document_docx_duplicate_entry")
        seen_names.add(normalized_name)
        if not is_directory:
            present_files.add(normalized_name)
        entry_count += 1
        if entry_count > settings.max_docx_entries:
            raise DocumentEnvelopeViolation(code="document_docx_entry_limit_exceeded")
        if uncompressed_size > settings.max_docx_member_uncompressed_bytes:
            raise DocumentEnvelopeViolation(code="document_docx_member_size_exceeded")
        total_uncompressed_bytes += uncompressed_size
        if total_uncompressed_bytes > settings.max_docx_declared_uncompressed_bytes:
            raise DocumentEnvelopeViolation(code="document_docx_declared_size_exceeded")
        if uncompressed_size > 0 and (
            compressed_size == 0
            or uncompressed_size / compressed_size > settings.max_docx_member_compression_ratio
        ):
            raise DocumentEnvelopeViolation(code="document_docx_compression_ratio_exceeded")
        offset = entry_end
    if entry_count != expected_entry_count or offset != len(content):
        raise DocumentEnvelopeViolation(code="document_docx_zip_invalid")
    if not _DOCX_REQUIRED_ENTRIES <= present_files:
        raise DocumentEnvelopeViolation(code="document_docx_required_entry_missing")


def _validate_zip_extra_fields(content: bytes) -> None:
    offset = 0
    while offset < len(content):
        if len(content) - offset < 4:
            raise DocumentEnvelopeViolation(code="document_docx_zip_invalid")
        field_id = int.from_bytes(content[offset : offset + 2], "little")
        field_size = int.from_bytes(content[offset + 2 : offset + 4], "little")
        offset += 4
        if field_id == _ZIP64_EXTRA_FIELD_ID or offset + field_size > len(content):
            raise DocumentEnvelopeViolation(code="document_docx_zip_invalid")
        offset += field_size


def _validate_docx_name(name: str) -> tuple[str, bool]:
    if not name or "\x00" in name or "\\" in name:
        raise DocumentEnvelopeViolation(code="document_docx_path_unsafe")
    is_directory = name.endswith("/")
    path = name[:-1] if is_directory else name
    if not path or path.startswith("/") or _WINDOWS_DRIVE_PREFIX.match(path):
        raise DocumentEnvelopeViolation(code="document_docx_path_unsafe")
    if any(part in {"", ".", ".."} for part in path.split("/")):
        raise DocumentEnvelopeViolation(code="document_docx_path_unsafe")
    normalized = unicodedata.normalize("NFC", path)
    return (f"{normalized}/" if is_directory else normalized, is_directory)
