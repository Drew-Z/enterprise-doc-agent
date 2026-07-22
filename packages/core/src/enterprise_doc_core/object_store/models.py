from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class PresignedUploadPart:
    url: str
    headers: Mapping[str, str]
    expires_in_seconds: int


@dataclass(frozen=True, slots=True)
class PresignedObjectDownload:
    url: str
    expires_in_seconds: int


@dataclass(frozen=True, slots=True)
class UploadedPart:
    part_number: int
    size_bytes: int
    etag: str
    checksum_sha256_b64: str | None


@dataclass(frozen=True, slots=True)
class CompletedMultipartUpload:
    etag: str
    checksum_sha256_b64: str | None


@dataclass(frozen=True, slots=True)
class ObjectHead:
    size_bytes: int
    etag: str
    checksum_sha256_b64: str | None
    content_type: str | None
    metadata: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ArtifactObject:
    bucket: str
    key: str
    size_bytes: int
    content_sha256: str
    content_type: str
    etag: str


@dataclass(frozen=True, slots=True)
class IncompleteUpload:
    key: str
    upload_id: str
    initiated_at: datetime | None
