from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from uuid import UUID

from enterprise_doc_core.config import UploadSettings

MIB = 1024**2
S3_MIN_PART_SIZE_BYTES = 5 * MIB
S3_MAX_PART_SIZE_BYTES = 5 * 1024**3
S3_MAX_PART_COUNT = 10_000
S3_MAX_OBJECT_SIZE_BYTES = 5 * 1024**4

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
ALLOWED_MEDIA_TYPES = {
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".docx": DOCX_MEDIA_TYPE,
}
WINDOWS_RESERVED_BASENAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
    *(f"COM{number}" for number in ("\u00b9", "\u00b2", "\u00b3")),
    *(f"LPT{number}" for number in ("\u00b9", "\u00b2", "\u00b3")),
}
WINDOWS_UNSAFE_CHARACTERS = frozenset('<>:"/\\|?*')
LOWERCASE_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class UploadPolicyViolation(ValueError):
    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class MultipartPlan:
    part_size_bytes: int
    part_count: int


@dataclass(frozen=True, slots=True)
class ValidatedUploadMetadata:
    filename: str
    extension: str
    media_type: str
    size_bytes: int
    sha256: str
    part_size_bytes: int
    part_count: int
    request_fingerprint: str


def plan_multipart_upload(
    *,
    size_bytes: int,
    preferred_part_size_bytes: int,
) -> MultipartPlan:
    if size_bytes <= 0:
        raise UploadPolicyViolation(
            code="upload_size_invalid",
            message="The upload size must be greater than zero.",
        )
    if size_bytes > S3_MAX_OBJECT_SIZE_BYTES:
        raise UploadPolicyViolation(
            code="upload_part_count_invalid",
            message="The file exceeds the S3 multipart object-size limit.",
        )
    if not S3_MIN_PART_SIZE_BYTES <= preferred_part_size_bytes <= S3_MAX_PART_SIZE_BYTES:
        raise UploadPolicyViolation(
            code="upload_part_size_invalid",
            message="The configured multipart size is outside the S3 limits.",
        )

    required_part_size = math.ceil(size_bytes / S3_MAX_PART_COUNT)
    selected_part_size = max(preferred_part_size_bytes, required_part_size)
    if selected_part_size > preferred_part_size_bytes:
        selected_part_size = math.ceil(selected_part_size / MIB) * MIB
    if selected_part_size > S3_MAX_PART_SIZE_BYTES:
        raise UploadPolicyViolation(
            code="upload_part_count_invalid",
            message="The file cannot be represented within the multipart part limit.",
        )

    part_count = math.ceil(size_bytes / selected_part_size)
    if not 1 <= part_count <= S3_MAX_PART_COUNT:
        raise UploadPolicyViolation(
            code="upload_part_count_invalid",
            message="The file cannot be represented within the multipart part limit.",
        )
    return MultipartPlan(part_size_bytes=selected_part_size, part_count=part_count)


def validate_upload_metadata(
    *,
    filename: str,
    size_bytes: int,
    media_type: str,
    sha256: str,
    settings: UploadSettings,
) -> ValidatedUploadMetadata:
    normalized_filename = unicodedata.normalize("NFC", filename)
    extension = _validate_filename(normalized_filename, settings.max_filename_length)
    normalized_media_type = media_type.strip().lower()
    expected_media_type = ALLOWED_MEDIA_TYPES.get(extension)
    if expected_media_type is None:
        raise UploadPolicyViolation(
            code="upload_type_unsupported",
            message="Only TXT, PDF, and DOCX uploads are supported.",
        )
    if normalized_media_type != expected_media_type:
        raise UploadPolicyViolation(
            code="upload_media_type_mismatch",
            message="The declared media type does not match the filename extension.",
        )
    if size_bytes <= 0:
        raise UploadPolicyViolation(
            code="upload_size_invalid",
            message="The upload size must be greater than zero.",
        )
    if size_bytes > settings.max_file_size_bytes:
        raise UploadPolicyViolation(
            code="upload_size_exceeded",
            message="The upload exceeds the configured size limit.",
        )
    if LOWERCASE_SHA256_PATTERN.fullmatch(sha256) is None:
        raise UploadPolicyViolation(
            code="upload_sha256_invalid",
            message="The whole-file SHA-256 must be 64 lowercase hexadecimal characters.",
        )

    plan = plan_multipart_upload(
        size_bytes=size_bytes,
        preferred_part_size_bytes=settings.preferred_part_size_bytes,
    )
    fingerprint = _request_fingerprint(
        filename=normalized_filename,
        extension=extension,
        media_type=normalized_media_type,
        size_bytes=size_bytes,
        sha256=sha256,
    )
    return ValidatedUploadMetadata(
        filename=normalized_filename,
        extension=extension,
        media_type=normalized_media_type,
        size_bytes=size_bytes,
        sha256=sha256,
        part_size_bytes=plan.part_size_bytes,
        part_count=plan.part_count,
        request_fingerprint=fingerprint,
    )


def build_object_key(*, session_id: UUID, version_id: UUID) -> str:
    return f"m1/uploads/{session_id.hex}/{version_id.hex}"


def _validate_filename(filename: str, max_length: int) -> str:
    unsafe = (
        not filename
        or len(filename) > max_length
        or filename != filename.strip()
        or filename.endswith((" ", "."))
        or all(character == "." for character in filename)
        or any(character in WINDOWS_UNSAFE_CHARACTERS for character in filename)
        or any(unicodedata.category(character).startswith("C") for character in filename)
    )
    stem, separator, suffix = filename.rpartition(".")
    if unsafe or not separator or not stem or not suffix:
        raise UploadPolicyViolation(
            code="upload_filename_unsafe",
            message="The filename is unsafe or invalid.",
        )
    reserved_basename = filename.split(".", maxsplit=1)[0].rstrip(" .").upper()
    if reserved_basename in WINDOWS_RESERVED_BASENAMES:
        raise UploadPolicyViolation(
            code="upload_filename_unsafe",
            message="The filename uses a reserved basename.",
        )
    return f".{suffix.lower()}"


def _request_fingerprint(
    *,
    filename: str,
    extension: str,
    media_type: str,
    size_bytes: int,
    sha256: str,
) -> str:
    canonical = json.dumps(
        {
            "extension": extension,
            "filename": filename,
            "mediaType": media_type,
            "sha256": sha256,
            "sizeBytes": size_bytes,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
