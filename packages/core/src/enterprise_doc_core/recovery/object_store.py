from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal, cast
from urllib.parse import unquote, urlsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.agents.models import AgentArtifact
from enterprise_doc_core.documents.models import DocumentVersion
from enterprise_doc_core.uploads.models import UploadSession

SNAPSHOT_ROOT = "enterprise-doc-recovery/snapshots"
RESTORE_ROOT = "enterprise-doc-recovery/restores"
DRILL_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
POSTGRES_IDENTIFIER_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
R2_SINGLE_COPY_MAX_BYTES = 5 * 1024**3 - 5 * 1024**2
R2_MULTIPART_COPY_PART_SIZE = 4 * 1024**3


class ObjectStoreRecoveryError(RuntimeError):
    """A sanitized recovery failure that never includes object keys or credentials."""


@dataclass(frozen=True, slots=True)
class ObjectReference:
    reference_type: Literal["document_version", "agent_artifact"]
    reference_id: UUID
    bucket: str
    key: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ObjectReferenceIdentity:
    reference_type: Literal["document_version", "agent_artifact"]
    reference_id: str

    def to_dict(self) -> dict[str, str]:
        return {
            "reference_id": self.reference_id,
            "reference_type": self.reference_type,
        }


@dataclass(frozen=True, slots=True)
class SnapshotObject:
    bucket: str
    source_key: str
    snapshot_key: str
    size_bytes: int
    sha256: str
    source_etag: str
    references: tuple[ObjectReferenceIdentity, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "bucket": self.bucket,
            "references": [reference.to_dict() for reference in self.references],
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "snapshot_key": self.snapshot_key,
            "source_etag": self.source_etag,
            "source_key": self.source_key,
        }


@dataclass(frozen=True, slots=True)
class SnapshotManifest:
    schema_version: int
    operation: str
    drill_id: str
    created_at: str
    endpoint_host: str
    snapshot_prefix: str
    manifest_bucket: str
    manifest_key: str
    objects: tuple[SnapshotObject, ...]
    manifest_sha256: str

    def payload_dict(self) -> dict[str, object]:
        return {
            "created_at": self.created_at,
            "drill_id": self.drill_id,
            "endpoint_host": self.endpoint_host,
            "manifest_bucket": self.manifest_bucket,
            "manifest_key": self.manifest_key,
            "objects": [item.to_dict() for item in self.objects],
            "operation": self.operation,
            "schema_version": self.schema_version,
            "snapshot_prefix": self.snapshot_prefix,
        }

    def to_dict(self) -> dict[str, object]:
        return self.payload_dict() | {"manifest_sha256": self.manifest_sha256}

    def render(self) -> bytes:
        return _canonical_json(self.to_dict())


@dataclass(frozen=True, slots=True)
class SnapshotResult:
    status: Literal["planned", "passed"]
    object_count: int
    copied_count: int
    existing_count: int
    manifest: SnapshotManifest

    def to_record(self) -> dict[str, object]:
        return {
            "copied_count": self.copied_count,
            "endpoint_host": self.manifest.endpoint_host,
            "existing_count": self.existing_count,
            "manifest_bucket": self.manifest.manifest_bucket,
            "manifest_sha256": self.manifest.manifest_sha256,
            "object_count": self.object_count,
            "operation": "r2-object-snapshot",
            "schema_version": 1,
            "snapshot_prefix": self.manifest.snapshot_prefix,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class RestoreResult:
    status: Literal["planned", "passed"]
    object_count: int
    copied_count: int
    existing_count: int
    manifest_sha256: str
    endpoint_host: str
    restore_prefix: str
    database_name: str

    def to_record(self) -> dict[str, object]:
        return {
            "copied_count": self.copied_count,
            "database_name": self.database_name,
            "endpoint_host": self.endpoint_host,
            "existing_count": self.existing_count,
            "manifest_sha256": self.manifest_sha256,
            "object_count": self.object_count,
            "operation": "r2-object-restore",
            "restore_prefix": self.restore_prefix,
            "schema_version": 1,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class RestoreReferenceLocation:
    reference_type: Literal["document_version", "agent_artifact"]
    reference_id: str
    bucket: str
    source_key: str
    restore_key: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class RestoreRemapPlan:
    state: Literal["source", "restored"]
    restore_prefix: str
    locations: dict[tuple[str, str], RestoreReferenceLocation]
    upload_session_locations: dict[str, RestoreReferenceLocation]
    manifest_object_count: int
    document_version_count: int
    upload_session_count: int
    agent_artifact_count: int

    @property
    def object_count(self) -> int:
        return self.manifest_object_count

    @property
    def reference_count(self) -> int:
        return self.document_version_count + self.upload_session_count + self.agent_artifact_count


@dataclass(frozen=True, slots=True)
class RestoreRemapResult:
    status: Literal["planned", "passed"]
    initial_state: Literal["source", "restored"]
    final_state: Literal["source", "restored"]
    database_name: str
    restore_prefix: str
    manifest_sha256: str
    object_count: int
    document_version_count: int
    upload_session_count: int
    agent_artifact_count: int
    updated_reference_count: int

    def to_record(self) -> dict[str, object]:
        return {
            "agent_artifact_count": self.agent_artifact_count,
            "database_name": self.database_name,
            "document_version_count": self.document_version_count,
            "final_state": self.final_state,
            "initial_state": self.initial_state,
            "manifest_sha256": self.manifest_sha256,
            "object_count": self.object_count,
            "operation": "r2-object-reference-remap",
            "restore_prefix": self.restore_prefix,
            "schema_version": 1,
            "status": self.status,
            "updated_reference_count": self.updated_reference_count,
            "upload_session_count": self.upload_session_count,
        }


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"


def _payload_sha256(value: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _normalized_host(value: str) -> str:
    normalized = value.strip().lower().rstrip(".")
    if not normalized or "/" in normalized or ":" in normalized:
        raise ObjectStoreRecoveryError("object-store endpoint host is invalid")
    return normalized


def endpoint_host_from_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ObjectStoreRecoveryError("object-store endpoint URL is invalid")
    return _normalized_host(parsed.hostname)


def database_name_from_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme.split("+", maxsplit=1)[0] not in {"postgres", "postgresql"}:
        raise ObjectStoreRecoveryError("database URL must use PostgreSQL")
    database_name = unquote(parsed.path.lstrip("/"))
    if POSTGRES_IDENTIFIER_PATTERN.fullmatch(database_name) is None:
        raise ObjectStoreRecoveryError("database URL has an invalid database name")
    return database_name


def _validated_drill_id(value: str) -> str:
    if DRILL_ID_PATTERN.fullmatch(value) is None:
        raise ObjectStoreRecoveryError(
            "drill ID must contain lowercase letters, digits, or hyphens"
        )
    return value


def _object_identity(bucket: str, key: str) -> str:
    return hashlib.sha256(f"{bucket}\0{key}".encode()).hexdigest()


def _snapshot_prefix(drill_id: str) -> str:
    return f"{SNAPSHOT_ROOT}/{_validated_drill_id(drill_id)}"


def _restore_prefix(restore_id: str) -> str:
    return f"{RESTORE_ROOT}/{_validated_drill_id(restore_id)}"


def _stream_sha256(body: Any) -> str:
    digest = hashlib.sha256()
    try:
        while True:
            block = body.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    finally:
        close = getattr(body, "close", None)
        if callable(close):
            close()
    return digest.hexdigest()


def _read_object_sha256(client: Any, *, bucket: str, key: str) -> str:
    try:
        response = client.get_object(Bucket=bucket, Key=key)
        return _stream_sha256(response["Body"])
    except Exception as error:
        raise ObjectStoreRecoveryError("object-store read failed") from error


def _read_object_bytes(client: Any, *, bucket: str, key: str) -> bytes:
    try:
        response = client.get_object(Bucket=bucket, Key=key)
        body = response["Body"]
        try:
            value = body.read()
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()
    except Exception as error:
        raise ObjectStoreRecoveryError("object-store read failed") from error
    if not isinstance(value, bytes):
        raise ObjectStoreRecoveryError("object-store read returned invalid bytes")
    return value


def _head_object(client: Any, *, bucket: str, key: str) -> Mapping[str, Any]:
    try:
        response = client.head_object(Bucket=bucket, Key=key)
    except Exception as error:
        raise ObjectStoreRecoveryError("object-store head failed") from error
    if not isinstance(response, Mapping):
        raise ObjectStoreRecoveryError("object-store head returned an invalid response")
    return response


def _optional_head_object(client: Any, *, bucket: str, key: str) -> Mapping[str, Any] | None:
    try:
        response = client.head_object(Bucket=bucket, Key=key)
    except (KeyError, FileNotFoundError):
        return None
    except Exception as error:
        error_response = getattr(error, "response", None)
        error_payload = error_response.get("Error") if isinstance(error_response, Mapping) else None
        response_code = error_payload.get("Code") if isinstance(error_payload, Mapping) else None
        if response_code in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise ObjectStoreRecoveryError("object-store head failed") from error
    if not isinstance(response, Mapping):
        raise ObjectStoreRecoveryError("object-store head returned an invalid response")
    return response


def _etag(head: Mapping[str, Any]) -> str:
    value = head.get("ETag")
    if not isinstance(value, str) or not value:
        raise ObjectStoreRecoveryError("object-store head did not return an ETag")
    return value


def _content_length(head: Mapping[str, Any]) -> int:
    value = head.get("ContentLength")
    if not isinstance(value, int) or value < 0:
        raise ObjectStoreRecoveryError("object-store head did not return a valid size")
    return value


def _group_references(
    references: Iterable[ObjectReference],
    *,
    allowed_buckets: frozenset[str],
    snapshot_prefix: str,
) -> tuple[SnapshotObject, ...]:
    grouped: dict[tuple[str, str], list[ObjectReference]] = {}
    for reference in references:
        if reference.bucket not in allowed_buckets:
            raise ObjectStoreRecoveryError("database reference uses a bucket outside the allowlist")
        if not reference.key or reference.key.startswith(f"{SNAPSHOT_ROOT}/"):
            raise ObjectStoreRecoveryError("database reference uses a reserved or invalid key")
        if reference.size_bytes < 0 or SHA256_PATTERN.fullmatch(reference.sha256) is None:
            raise ObjectStoreRecoveryError("database reference has invalid integrity metadata")
        grouped.setdefault((reference.bucket, reference.key), []).append(reference)

    objects: list[SnapshotObject] = []
    for (bucket, key), object_references in sorted(grouped.items()):
        first = object_references[0]
        if any(
            item.size_bytes != first.size_bytes or item.sha256 != first.sha256
            for item in object_references[1:]
        ):
            raise ObjectStoreRecoveryError("database references disagree on object integrity")
        identities = tuple(
            sorted(
                (
                    ObjectReferenceIdentity(
                        reference_type=item.reference_type,
                        reference_id=str(item.reference_id),
                    )
                    for item in object_references
                ),
                key=lambda item: (item.reference_type, item.reference_id),
            )
        )
        objects.append(
            SnapshotObject(
                bucket=bucket,
                source_key=key,
                snapshot_key=f"{snapshot_prefix}/objects/{_object_identity(bucket, key)}",
                size_bytes=first.size_bytes,
                sha256=first.sha256,
                source_etag="",
                references=identities,
            )
        )
    return tuple(objects)


def _verify_object(
    client: Any,
    *,
    bucket: str,
    key: str,
    expected_size: int,
    expected_sha256: str,
) -> Mapping[str, Any]:
    head = _head_object(client, bucket=bucket, key=key)
    if _content_length(head) != expected_size:
        raise ObjectStoreRecoveryError("object-store size does not match database metadata")
    if _read_object_sha256(client, bucket=bucket, key=key) != expected_sha256:
        raise ObjectStoreRecoveryError("object-store SHA-256 does not match database metadata")
    return head


def _multipart_create_arguments(
    *,
    bucket: str,
    key: str,
    source_head: Mapping[str, Any],
) -> dict[str, object]:
    arguments: dict[str, object] = {"Bucket": bucket, "Key": key}
    for name in (
        "CacheControl",
        "ContentDisposition",
        "ContentEncoding",
        "ContentLanguage",
        "ContentType",
        "Expires",
    ):
        value = source_head.get(name)
        if value is not None:
            arguments[name] = value
    metadata = source_head.get("Metadata")
    if isinstance(metadata, Mapping) and all(
        isinstance(metadata_key, str) and isinstance(metadata_value, str)
        for metadata_key, metadata_value in metadata.items()
    ):
        arguments["Metadata"] = dict(metadata)
    return arguments


def _copy_verified_object(
    client: Any,
    *,
    bucket: str,
    source_key: str,
    destination_key: str,
    size_bytes: int,
    source_head: Mapping[str, Any],
    single_copy_max_bytes: int,
    multipart_copy_part_size: int,
) -> None:
    if single_copy_max_bytes <= 0 or multipart_copy_part_size <= 0:
        raise ValueError("copy size limits must be positive")
    source_etag = _etag(source_head)
    if size_bytes <= single_copy_max_bytes:
        client.copy_object(
            Bucket=bucket,
            Key=destination_key,
            CopySource={"Bucket": bucket, "Key": source_key},
            CopySourceIfMatch=source_etag,
            MetadataDirective="COPY",
        )
        return

    response = client.create_multipart_upload(
        **_multipart_create_arguments(
            bucket=bucket,
            key=destination_key,
            source_head=source_head,
        )
    )
    upload_id = response.get("UploadId") if isinstance(response, Mapping) else None
    if not isinstance(upload_id, str) or not upload_id:
        raise ObjectStoreRecoveryError("multipart copy did not return an upload ID")
    completed_parts: list[dict[str, object]] = []
    try:
        part_number = 1
        for start in range(0, size_bytes, multipart_copy_part_size):
            end = min(start + multipart_copy_part_size, size_bytes) - 1
            part_response = client.upload_part_copy(
                Bucket=bucket,
                Key=destination_key,
                UploadId=upload_id,
                PartNumber=part_number,
                CopySource={"Bucket": bucket, "Key": source_key},
                CopySourceRange=f"bytes={start}-{end}",
            )
            copy_part_result = (
                part_response.get("CopyPartResult") if isinstance(part_response, Mapping) else None
            )
            part_etag = (
                copy_part_result.get("ETag") if isinstance(copy_part_result, Mapping) else None
            )
            if not isinstance(part_etag, str) or not part_etag:
                raise ObjectStoreRecoveryError("multipart copy part did not return an ETag")
            completed_parts.append({"ETag": part_etag, "PartNumber": part_number})
            part_number += 1
        client.complete_multipart_upload(
            Bucket=bucket,
            Key=destination_key,
            UploadId=upload_id,
            MultipartUpload={"Parts": completed_parts},
        )
        if _etag(_head_object(client, bucket=bucket, key=source_key)) != source_etag:
            raise ObjectStoreRecoveryError("multipart copy source changed during the operation")
    except Exception:
        try:
            client.abort_multipart_upload(
                Bucket=bucket,
                Key=destination_key,
                UploadId=upload_id,
            )
        except Exception:
            pass
        raise


def _build_manifest(
    *,
    drill_id: str,
    endpoint_host: str,
    manifest_bucket: str,
    manifest_key: str,
    snapshot_prefix: str,
    objects: tuple[SnapshotObject, ...],
    now: datetime,
) -> SnapshotManifest:
    manifest = SnapshotManifest(
        schema_version=1,
        operation="r2-object-snapshot",
        drill_id=drill_id,
        created_at=now.astimezone(UTC).isoformat(),
        endpoint_host=endpoint_host,
        snapshot_prefix=snapshot_prefix,
        manifest_bucket=manifest_bucket,
        manifest_key=manifest_key,
        objects=objects,
        manifest_sha256="",
    )
    return replace(manifest, manifest_sha256=_payload_sha256(manifest.payload_dict()))


def _required_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ObjectStoreRecoveryError("snapshot manifest has an invalid object")
    return value


def _required_string(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ObjectStoreRecoveryError("snapshot manifest has an invalid string")
    return value


def _required_integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ObjectStoreRecoveryError("snapshot manifest has an invalid integer")
    return value


def parse_snapshot_manifest(body: bytes) -> SnapshotManifest:
    try:
        raw = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ObjectStoreRecoveryError("snapshot manifest is not valid JSON") from error
    root = _required_mapping(raw)
    expected_root_keys = {
        "created_at",
        "drill_id",
        "endpoint_host",
        "manifest_bucket",
        "manifest_key",
        "manifest_sha256",
        "objects",
        "operation",
        "schema_version",
        "snapshot_prefix",
    }
    if set(root) != expected_root_keys:
        raise ObjectStoreRecoveryError("snapshot manifest fields do not match schema version 1")
    if root["schema_version"] != 1 or root["operation"] != "r2-object-snapshot":
        raise ObjectStoreRecoveryError("snapshot manifest contract is unsupported")

    drill_id = _validated_drill_id(_required_string(root["drill_id"]))
    snapshot_prefix = _required_string(root["snapshot_prefix"])
    if snapshot_prefix != _snapshot_prefix(drill_id):
        raise ObjectStoreRecoveryError("snapshot manifest prefix does not match the drill ID")
    manifest_key = _required_string(root["manifest_key"])
    if manifest_key != f"{snapshot_prefix}/manifest.json":
        raise ObjectStoreRecoveryError("snapshot manifest key does not match the snapshot prefix")
    created_at = _required_string(root["created_at"])
    try:
        parsed_created_at = datetime.fromisoformat(created_at)
    except ValueError as error:
        raise ObjectStoreRecoveryError("snapshot manifest timestamp is invalid") from error
    if parsed_created_at.tzinfo is None or parsed_created_at.utcoffset() is None:
        raise ObjectStoreRecoveryError("snapshot manifest timestamp must include a timezone")

    raw_objects = root["objects"]
    if not isinstance(raw_objects, list):
        raise ObjectStoreRecoveryError("snapshot manifest objects must be a list")
    objects: list[SnapshotObject] = []
    for raw_object in raw_objects:
        item = _required_mapping(raw_object)
        if set(item) != {
            "bucket",
            "references",
            "sha256",
            "size_bytes",
            "snapshot_key",
            "source_etag",
            "source_key",
        }:
            raise ObjectStoreRecoveryError("snapshot manifest object fields are invalid")
        bucket = _required_string(item["bucket"])
        source_key = _required_string(item["source_key"])
        snapshot_key = _required_string(item["snapshot_key"])
        if snapshot_key != f"{snapshot_prefix}/objects/{_object_identity(bucket, source_key)}":
            raise ObjectStoreRecoveryError(
                "snapshot manifest contains an invalid object key mapping"
            )
        sha256 = _required_string(item["sha256"])
        if SHA256_PATTERN.fullmatch(sha256) is None:
            raise ObjectStoreRecoveryError("snapshot manifest contains an invalid SHA-256")
        raw_references = item["references"]
        if not isinstance(raw_references, list) or not raw_references:
            raise ObjectStoreRecoveryError("snapshot manifest object has no database references")
        references: list[ObjectReferenceIdentity] = []
        for raw_reference in raw_references:
            reference = _required_mapping(raw_reference)
            if set(reference) != {"reference_id", "reference_type"}:
                raise ObjectStoreRecoveryError("snapshot manifest reference fields are invalid")
            reference_type = _required_string(reference["reference_type"])
            if reference_type not in {"document_version", "agent_artifact"}:
                raise ObjectStoreRecoveryError("snapshot manifest reference type is invalid")
            reference_id = _required_string(reference["reference_id"])
            try:
                UUID(reference_id)
            except ValueError as error:
                raise ObjectStoreRecoveryError(
                    "snapshot manifest reference ID is invalid"
                ) from error
            references.append(
                ObjectReferenceIdentity(
                    reference_type=cast(
                        Literal["document_version", "agent_artifact"], reference_type
                    ),
                    reference_id=reference_id,
                )
            )
        objects.append(
            SnapshotObject(
                bucket=bucket,
                source_key=source_key,
                snapshot_key=snapshot_key,
                size_bytes=_required_integer(item["size_bytes"]),
                sha256=sha256,
                source_etag=_required_string(item["source_etag"]),
                references=tuple(references),
            )
        )

    manifest_sha256 = _required_string(root["manifest_sha256"])
    if SHA256_PATTERN.fullmatch(manifest_sha256) is None:
        raise ObjectStoreRecoveryError("snapshot manifest digest is invalid")
    manifest = SnapshotManifest(
        schema_version=1,
        operation="r2-object-snapshot",
        drill_id=drill_id,
        created_at=created_at,
        endpoint_host=_normalized_host(_required_string(root["endpoint_host"])),
        snapshot_prefix=snapshot_prefix,
        manifest_bucket=_required_string(root["manifest_bucket"]),
        manifest_key=manifest_key,
        objects=tuple(objects),
        manifest_sha256=manifest_sha256,
    )
    if _payload_sha256(manifest.payload_dict()) != manifest.manifest_sha256:
        raise ObjectStoreRecoveryError("snapshot manifest digest does not match its payload")
    return manifest


def _assert_manifest_matches_references(
    manifest: SnapshotManifest,
    expected_objects: tuple[SnapshotObject, ...],
    *,
    endpoint_host: str,
    manifest_bucket: str,
    allowed_buckets: frozenset[str],
) -> None:
    if manifest.endpoint_host != endpoint_host:
        raise ObjectStoreRecoveryError("snapshot manifest endpoint host does not match")
    if manifest.manifest_bucket != manifest_bucket:
        raise ObjectStoreRecoveryError("snapshot manifest bucket does not match")
    if any(item.bucket not in allowed_buckets for item in manifest.objects):
        raise ObjectStoreRecoveryError("snapshot manifest uses a bucket outside the allowlist")
    expected = {
        (item.bucket, item.source_key): (
            item.snapshot_key,
            item.size_bytes,
            item.sha256,
            item.references,
        )
        for item in expected_objects
    }
    observed = {
        (item.bucket, item.source_key): (
            item.snapshot_key,
            item.size_bytes,
            item.sha256,
            item.references,
        )
        for item in manifest.objects
    }
    if expected != observed or len(observed) != len(manifest.objects):
        raise ObjectStoreRecoveryError("snapshot manifest does not match database references")


async def load_object_references(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    documents_bucket: str,
) -> tuple[ObjectReference, ...]:
    async with session_factory() as session:
        document_versions = (await session.scalars(select(DocumentVersion))).all()
        artifacts = (
            await session.scalars(
                select(AgentArtifact).where(
                    AgentArtifact.status.in_(("draft_ready", "published", "revoked"))
                )
            )
        ).all()

    references = [
        ObjectReference(
            reference_type="document_version",
            reference_id=version.id,
            bucket=documents_bucket,
            key=version.object_key,
            size_bytes=version.size_bytes,
            sha256=version.declared_sha256,
        )
        for version in document_versions
    ]
    for artifact in artifacts:
        if artifact.content_sha256 is None or artifact.size_bytes is None:
            raise ObjectStoreRecoveryError(
                "durable Agent artifact is missing database integrity metadata"
            )
        references.append(
            ObjectReference(
                reference_type="agent_artifact",
                reference_id=artifact.id,
                bucket=artifact.object_bucket,
                key=artifact.object_key,
                size_bytes=artifact.size_bytes,
                sha256=artifact.content_sha256,
            )
        )
    return tuple(references)


def _list_object_keys(client: Any, *, bucket: str, prefix: str) -> frozenset[str]:
    keys: set[str] = set()
    continuation_token: str | None = None
    while True:
        arguments: dict[str, object] = {"Bucket": bucket, "Prefix": prefix}
        if continuation_token is not None:
            arguments["ContinuationToken"] = continuation_token
        try:
            response = client.list_objects_v2(**arguments)
        except Exception as error:
            raise ObjectStoreRecoveryError("object-store inventory listing failed") from error
        if not isinstance(response, Mapping):
            raise ObjectStoreRecoveryError("object-store inventory response is invalid")
        contents = response.get("Contents", [])
        if not isinstance(contents, list):
            raise ObjectStoreRecoveryError("object-store inventory contents are invalid")
        for raw_item in contents:
            item = _required_mapping(raw_item)
            key = item.get("Key")
            if not isinstance(key, str) or not key.startswith(prefix):
                raise ObjectStoreRecoveryError("object-store inventory contains an invalid key")
            keys.add(key)
        if response.get("IsTruncated") is not True:
            return frozenset(keys)
        next_token = response.get("NextContinuationToken")
        if not isinstance(next_token, str) or not next_token:
            raise ObjectStoreRecoveryError("object-store inventory pagination is invalid")
        continuation_token = next_token


def _validate_restore_database(database_name: str, expected_database_name: str) -> None:
    if (
        POSTGRES_IDENTIFIER_PATTERN.fullmatch(database_name) is None
        or POSTGRES_IDENTIFIER_PATTERN.fullmatch(expected_database_name) is None
    ):
        raise ObjectStoreRecoveryError("restore database name is invalid")
    if database_name != expected_database_name:
        raise ObjectStoreRecoveryError("restore database name does not match expectation")
    if not database_name.startswith("enterprise_doc_restore_"):
        raise ObjectStoreRecoveryError("object restore requires an isolated restore database")


def _manifest_remap_locations(
    manifest: SnapshotManifest,
    *,
    restore_id: str,
    allowed_buckets: frozenset[str],
) -> tuple[str, dict[tuple[str, str], RestoreReferenceLocation]]:
    if not allowed_buckets:
        raise ObjectStoreRecoveryError("bucket allowlist must not be empty")
    restore_prefix = _restore_prefix(restore_id)
    locations: dict[tuple[str, str], RestoreReferenceLocation] = {}
    for item in manifest.objects:
        if item.bucket not in allowed_buckets:
            raise ObjectStoreRecoveryError("snapshot manifest uses a bucket outside the allowlist")
        restore_key = f"{restore_prefix}/objects/{_object_identity(item.bucket, item.source_key)}"
        for reference in item.references:
            identity = (reference.reference_type, reference.reference_id)
            if identity in locations:
                raise ObjectStoreRecoveryError("snapshot manifest contains duplicate references")
            locations[identity] = RestoreReferenceLocation(
                reference_type=reference.reference_type,
                reference_id=reference.reference_id,
                bucket=item.bucket,
                source_key=item.source_key,
                restore_key=restore_key,
                size_bytes=item.size_bytes,
                sha256=item.sha256,
            )
    return restore_prefix, locations


def _key_state(
    current_key: str,
    location: RestoreReferenceLocation,
) -> Literal["source", "restored"]:
    if current_key == location.source_key:
        return "source"
    if current_key == location.restore_key:
        return "restored"
    raise ObjectStoreRecoveryError(
        "database object reference is outside the reviewed restore mapping"
    )


def _build_remap_plan(
    manifest: SnapshotManifest,
    *,
    restore_id: str,
    expected_manifest_sha256: str,
    database_name: str,
    expected_database_name: str,
    documents_bucket: str,
    allowed_buckets: frozenset[str],
    document_versions: Iterable[Any],
    upload_sessions: Iterable[Any],
    artifacts: Iterable[Any],
) -> RestoreRemapPlan:
    if (
        SHA256_PATTERN.fullmatch(expected_manifest_sha256) is None
        or expected_manifest_sha256 != manifest.manifest_sha256
        or _payload_sha256(manifest.payload_dict()) != manifest.manifest_sha256
    ):
        raise ObjectStoreRecoveryError("snapshot manifest digest does not match expectation")
    _validate_restore_database(database_name, expected_database_name)
    if documents_bucket not in allowed_buckets:
        raise ObjectStoreRecoveryError("documents bucket must be in the bucket allowlist")

    restore_prefix, locations = _manifest_remap_locations(
        manifest,
        restore_id=restore_id,
        allowed_buckets=allowed_buckets,
    )
    document_rows = tuple(document_versions)
    upload_rows = tuple(upload_sessions)
    artifact_rows = tuple(artifacts)
    expected_reference_count = len(document_rows) + len(artifact_rows)
    if len(locations) != expected_reference_count:
        raise ObjectStoreRecoveryError("snapshot manifest does not match database references")

    states: set[Literal["source", "restored"]] = set()
    document_locations: dict[str, RestoreReferenceLocation] = {}
    for version in document_rows:
        identity = ("document_version", str(version.id))
        location = locations.get(identity)
        if location is None or location.bucket != documents_bucket:
            raise ObjectStoreRecoveryError("snapshot manifest does not match database references")
        if version.size_bytes != location.size_bytes or version.declared_sha256 != location.sha256:
            raise ObjectStoreRecoveryError("snapshot manifest does not match database references")
        document_locations[str(version.id)] = location
        states.add(_key_state(version.object_key, location))

    for artifact in artifact_rows:
        identity = ("agent_artifact", str(artifact.id))
        location = locations.get(identity)
        if location is None or location.bucket != artifact.object_bucket:
            raise ObjectStoreRecoveryError("snapshot manifest does not match database references")
        if artifact.size_bytes != location.size_bytes or artifact.content_sha256 != location.sha256:
            raise ObjectStoreRecoveryError("snapshot manifest does not match database references")
        states.add(_key_state(artifact.object_key, location))

    sessions_by_id = {str(session.id): session for session in upload_rows}
    if len(sessions_by_id) != len(upload_rows):
        raise ObjectStoreRecoveryError("database contains duplicate upload session rows")
    upload_session_locations: dict[str, RestoreReferenceLocation] = {}
    for version in document_rows:
        session = sessions_by_id.get(str(version.upload_session_id))
        if session is None:
            raise ObjectStoreRecoveryError("document version is missing its upload session")
        location = document_locations[str(version.id)]
        session_state = _key_state(session.object_key, location)
        if session_state != _key_state(version.object_key, location):
            raise ObjectStoreRecoveryError("document and upload references are in different states")
        upload_session_locations[str(session.id)] = location
        states.add(session_state)

    if len(states) > 1:
        raise ObjectStoreRecoveryError("database object references are in a mixed remap state")
    state: Literal["source", "restored"] = next(iter(states), "source")
    return RestoreRemapPlan(
        state=state,
        restore_prefix=restore_prefix,
        locations=locations,
        upload_session_locations=upload_session_locations,
        manifest_object_count=len(manifest.objects),
        document_version_count=len(document_rows),
        upload_session_count=len(upload_rows),
        agent_artifact_count=len(artifact_rows),
    )


def _verify_restored_inventory(
    client: Any,
    *,
    manifest: SnapshotManifest,
    restore_prefix: str,
) -> None:
    expected_by_bucket: dict[str, set[str]] = {}
    for item in manifest.objects:
        restore_key = f"{restore_prefix}/objects/{_object_identity(item.bucket, item.source_key)}"
        _verify_object(
            client,
            bucket=item.bucket,
            key=restore_key,
            expected_size=item.size_bytes,
            expected_sha256=item.sha256,
        )
        expected_by_bucket.setdefault(item.bucket, set()).add(restore_key)
    for bucket, expected_keys in expected_by_bucket.items():
        observed_keys = _list_object_keys(
            client,
            bucket=bucket,
            prefix=f"{restore_prefix}/",
        )
        if observed_keys != expected_keys:
            raise ObjectStoreRecoveryError(
                "restored object inventory does not match the remap manifest"
            )


def _apply_remap(
    plan: RestoreRemapPlan,
    *,
    document_versions: Iterable[Any],
    upload_sessions: Iterable[Any],
    artifacts: Iterable[Any],
) -> None:
    for version in document_versions:
        version.object_key = plan.locations[("document_version", str(version.id))].restore_key
    for session in upload_sessions:
        session.object_key = plan.upload_session_locations[str(session.id)].restore_key
    for artifact in artifacts:
        artifact.object_key = plan.locations[("agent_artifact", str(artifact.id))].restore_key


async def remap_restore_references(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    client: Any,
    manifest: SnapshotManifest,
    expected_manifest_sha256: str,
    endpoint_host: str,
    expected_endpoint_host: str,
    allowed_buckets: frozenset[str],
    documents_bucket: str,
    database_name: str,
    expected_database_name: str,
    restore_id: str,
    confirm: bool,
) -> RestoreRemapResult:
    endpoint_host = _normalized_host(endpoint_host)
    if endpoint_host != _normalized_host(expected_endpoint_host):
        raise ObjectStoreRecoveryError("object-store endpoint host does not match expectation")

    async with session_factory() as session:
        async with session.begin():
            document_versions = (
                await session.scalars(select(DocumentVersion).with_for_update())
            ).all()
            artifacts = (
                await session.scalars(
                    select(AgentArtifact)
                    .where(AgentArtifact.status.in_(("draft_ready", "published", "revoked")))
                    .with_for_update()
                )
            ).all()
            upload_ids = [version.upload_session_id for version in document_versions]
            upload_sessions = (
                (
                    await session.scalars(
                        select(UploadSession)
                        .where(UploadSession.id.in_(upload_ids))
                        .with_for_update()
                    )
                ).all()
                if upload_ids
                else []
            )
            plan = _build_remap_plan(
                manifest,
                restore_id=restore_id,
                expected_manifest_sha256=expected_manifest_sha256,
                database_name=database_name,
                expected_database_name=expected_database_name,
                documents_bucket=documents_bucket,
                allowed_buckets=allowed_buckets,
                document_versions=document_versions,
                upload_sessions=upload_sessions,
                artifacts=artifacts,
            )
            _verify_restored_inventory(
                client,
                manifest=manifest,
                restore_prefix=plan.restore_prefix,
            )
            if confirm and plan.state == "source":
                _apply_remap(
                    plan,
                    document_versions=document_versions,
                    upload_sessions=upload_sessions,
                    artifacts=artifacts,
                )
                await session.flush()
            return RestoreRemapResult(
                status="passed" if confirm else "planned",
                initial_state=plan.state,
                final_state="restored" if confirm else plan.state,
                database_name=database_name,
                restore_prefix=plan.restore_prefix,
                manifest_sha256=manifest.manifest_sha256,
                object_count=plan.object_count,
                document_version_count=plan.document_version_count,
                upload_session_count=plan.upload_session_count,
                agent_artifact_count=plan.agent_artifact_count,
                updated_reference_count=(
                    plan.reference_count if confirm and plan.state == "source" else 0
                ),
            )


def create_snapshot(
    *,
    client: Any,
    endpoint_host: str,
    expected_endpoint_host: str,
    allowed_buckets: frozenset[str],
    manifest_bucket: str,
    drill_id: str,
    references: Iterable[ObjectReference],
    confirm: bool,
    now: datetime | None = None,
    single_copy_max_bytes: int = R2_SINGLE_COPY_MAX_BYTES,
    multipart_copy_part_size: int = R2_MULTIPART_COPY_PART_SIZE,
) -> SnapshotResult:
    endpoint_host = _normalized_host(endpoint_host)
    if endpoint_host != _normalized_host(expected_endpoint_host):
        raise ObjectStoreRecoveryError("object-store endpoint host does not match expectation")
    if not allowed_buckets or manifest_bucket not in allowed_buckets:
        raise ObjectStoreRecoveryError("manifest bucket must be in the non-empty bucket allowlist")

    snapshot_prefix = _snapshot_prefix(drill_id)
    manifest_key = f"{snapshot_prefix}/manifest.json"
    objects = _group_references(
        references,
        allowed_buckets=allowed_buckets,
        snapshot_prefix=snapshot_prefix,
    )
    existing_manifest = _optional_head_object(
        client,
        bucket=manifest_bucket,
        key=manifest_key,
    )
    if existing_manifest is not None:
        manifest = parse_snapshot_manifest(
            _read_object_bytes(client, bucket=manifest_bucket, key=manifest_key)
        )
        _assert_manifest_matches_references(
            manifest,
            objects,
            endpoint_host=endpoint_host,
            manifest_bucket=manifest_bucket,
            allowed_buckets=allowed_buckets,
        )
        for item in manifest.objects:
            _verify_object(
                client,
                bucket=item.bucket,
                key=item.snapshot_key,
                expected_size=item.size_bytes,
                expected_sha256=item.sha256,
            )
        return SnapshotResult(
            status="passed" if confirm else "planned",
            object_count=len(manifest.objects),
            copied_count=0,
            existing_count=len(manifest.objects),
            manifest=manifest,
        )

    verified: list[SnapshotObject] = []
    for item in objects:
        source_head = _verify_object(
            client,
            bucket=item.bucket,
            key=item.source_key,
            expected_size=item.size_bytes,
            expected_sha256=item.sha256,
        )
        verified.append(replace(item, source_etag=_etag(source_head)))

    manifest = _build_manifest(
        drill_id=drill_id,
        endpoint_host=endpoint_host,
        manifest_bucket=manifest_bucket,
        manifest_key=manifest_key,
        snapshot_prefix=snapshot_prefix,
        objects=tuple(verified),
        now=now or datetime.now(UTC),
    )
    if not confirm:
        return SnapshotResult(
            status="planned",
            object_count=len(verified),
            copied_count=0,
            existing_count=0,
            manifest=manifest,
        )

    copied_count = 0
    existing_count = 0
    for item in verified:
        existing = _optional_head_object(client, bucket=item.bucket, key=item.snapshot_key)
        if existing is None:
            source_head = _head_object(client, bucket=item.bucket, key=item.source_key)
            if _etag(source_head) != item.source_etag:
                raise ObjectStoreRecoveryError("snapshot source changed after verification")
            try:
                _copy_verified_object(
                    client,
                    bucket=item.bucket,
                    source_key=item.source_key,
                    destination_key=item.snapshot_key,
                    size_bytes=item.size_bytes,
                    source_head=source_head,
                    single_copy_max_bytes=single_copy_max_bytes,
                    multipart_copy_part_size=multipart_copy_part_size,
                )
            except Exception as error:
                raise ObjectStoreRecoveryError("object-store snapshot copy failed") from error
            copied_count += 1
        else:
            existing_count += 1
        _verify_object(
            client,
            bucket=item.bucket,
            key=item.snapshot_key,
            expected_size=item.size_bytes,
            expected_sha256=item.sha256,
        )

    manifest_body = manifest.render()
    if _optional_head_object(client, bucket=manifest_bucket, key=manifest_key) is not None:
        raise ObjectStoreRecoveryError("snapshot manifest already exists")
    try:
        client.put_object(
            Bucket=manifest_bucket,
            Key=manifest_key,
            Body=manifest_body,
            ContentType="application/json",
            IfNoneMatch="*",
            Metadata={"sha256": hashlib.sha256(manifest_body).hexdigest()},
        )
    except Exception as error:
        raise ObjectStoreRecoveryError("snapshot manifest write failed") from error
    if (
        _read_object_sha256(client, bucket=manifest_bucket, key=manifest_key)
        != hashlib.sha256(manifest_body).hexdigest()
    ):
        raise ObjectStoreRecoveryError("snapshot manifest readback failed")
    return SnapshotResult(
        status="passed",
        object_count=len(verified),
        copied_count=copied_count,
        existing_count=existing_count,
        manifest=manifest,
    )


def restore_snapshot(
    *,
    client: Any,
    manifest: SnapshotManifest,
    expected_manifest_sha256: str,
    endpoint_host: str,
    expected_endpoint_host: str,
    allowed_buckets: frozenset[str],
    database_name: str,
    expected_database_name: str,
    restore_id: str,
    references: Iterable[ObjectReference],
    confirm: bool,
    single_copy_max_bytes: int = R2_SINGLE_COPY_MAX_BYTES,
    multipart_copy_part_size: int = R2_MULTIPART_COPY_PART_SIZE,
) -> RestoreResult:
    endpoint_host = _normalized_host(endpoint_host)
    if endpoint_host != _normalized_host(expected_endpoint_host):
        raise ObjectStoreRecoveryError("object-store endpoint host does not match expectation")
    if not allowed_buckets:
        raise ObjectStoreRecoveryError("bucket allowlist must not be empty")
    if (
        SHA256_PATTERN.fullmatch(expected_manifest_sha256) is None
        or expected_manifest_sha256 != manifest.manifest_sha256
        or _payload_sha256(manifest.payload_dict()) != manifest.manifest_sha256
    ):
        raise ObjectStoreRecoveryError("snapshot manifest digest does not match expectation")
    _validate_restore_database(database_name, expected_database_name)
    expected_objects = _group_references(
        references,
        allowed_buckets=allowed_buckets,
        snapshot_prefix=manifest.snapshot_prefix,
    )
    _assert_manifest_matches_references(
        manifest,
        expected_objects,
        endpoint_host=endpoint_host,
        manifest_bucket=manifest.manifest_bucket,
        allowed_buckets=allowed_buckets,
    )

    restore_prefix = _restore_prefix(restore_id)
    restore_keys: dict[tuple[str, str], str] = {}
    existing_count = 0
    missing: list[tuple[SnapshotObject, str, Mapping[str, Any]]] = []
    for item in manifest.objects:
        snapshot_head = _verify_object(
            client,
            bucket=item.bucket,
            key=item.snapshot_key,
            expected_size=item.size_bytes,
            expected_sha256=item.sha256,
        )
        restore_key = f"{restore_prefix}/objects/{_object_identity(item.bucket, item.source_key)}"
        restore_keys[(item.bucket, item.source_key)] = restore_key
        existing = _optional_head_object(client, bucket=item.bucket, key=restore_key)
        if existing is None:
            missing.append((item, restore_key, snapshot_head))
            continue
        _verify_object(
            client,
            bucket=item.bucket,
            key=restore_key,
            expected_size=item.size_bytes,
            expected_sha256=item.sha256,
        )
        existing_count += 1

    expected_by_bucket: dict[str, set[str]] = {}
    for (bucket, _), key in restore_keys.items():
        expected_by_bucket.setdefault(bucket, set()).add(key)
    for bucket, expected_keys in expected_by_bucket.items():
        observed_keys = _list_object_keys(
            client,
            bucket=bucket,
            prefix=f"{restore_prefix}/",
        )
        if not observed_keys.issubset(expected_keys):
            raise ObjectStoreRecoveryError("restore prefix contains unexpected objects")

    if not confirm:
        return RestoreResult(
            status="planned",
            object_count=len(manifest.objects),
            copied_count=0,
            existing_count=existing_count,
            manifest_sha256=manifest.manifest_sha256,
            endpoint_host=endpoint_host,
            restore_prefix=restore_prefix,
            database_name=database_name,
        )

    copied_count = 0
    for item, restore_key, snapshot_head in missing:
        try:
            _copy_verified_object(
                client,
                bucket=item.bucket,
                source_key=item.snapshot_key,
                destination_key=restore_key,
                size_bytes=item.size_bytes,
                source_head=snapshot_head,
                single_copy_max_bytes=single_copy_max_bytes,
                multipart_copy_part_size=multipart_copy_part_size,
            )
        except Exception as error:
            raise ObjectStoreRecoveryError("object-store restore copy failed") from error
        _verify_object(
            client,
            bucket=item.bucket,
            key=restore_key,
            expected_size=item.size_bytes,
            expected_sha256=item.sha256,
        )
        copied_count += 1

    for bucket, expected_keys in expected_by_bucket.items():
        observed_keys = _list_object_keys(
            client,
            bucket=bucket,
            prefix=f"{restore_prefix}/",
        )
        if observed_keys != expected_keys:
            raise ObjectStoreRecoveryError(
                "restored object inventory does not match database references"
            )
    return RestoreResult(
        status="passed",
        object_count=len(manifest.objects),
        copied_count=copied_count,
        existing_count=existing_count,
        manifest_sha256=manifest.manifest_sha256,
        endpoint_host=endpoint_host,
        restore_prefix=restore_prefix,
        database_name=database_name,
    )
