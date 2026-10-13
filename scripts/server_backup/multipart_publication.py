"""Bounded immutable multipart transfer, called under the backup runtime lock.

The sealed attempt is authoritative. A lost create response is reconciled by
listing its exact key; absence is uncertainty, never permission to create again.
The caller publishes the existing completion marker only after this returns.
"""

import base64
import contextlib
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from botocore.exceptions import ClientError

from .remote_retention import DEFAULT_MAX_BYTES, PREFIX, inventory
from .server_publication import (
    MAX_CIPHERTEXT_BYTES,
    PublicationError,
    _read_matches,
    marker_bytes,
    snapshot_record,
)

PART_BYTES = 8 * 1024 * 1024
WORKERS = 4


def _require(condition, message):
    if not condition:
        raise PublicationError(message)


def _etag(data):
    return '"' + hashlib.md5(data, usedforsecurity=False).hexdigest() + '"'


def _uploads(client, bucket):
    result = client.list_multipart_uploads(Bucket=bucket, Prefix=PREFIX, MaxUploads=64)
    _require(result.get("IsTruncated", False) is False, "multipart inventory exceeds budget")
    rows = result.get("Uploads", [])
    _require(isinstance(rows, list) and len(rows) <= 64, "invalid multipart inventory")
    seen = set()
    for row in rows:
        key, identity = row.get("Key"), row.get("UploadId")
        _require(
            isinstance(key, str)
            and key.startswith(PREFIX + "snapshot-")
            and key.endswith(".tar.age")
            and isinstance(identity, str)
            and 0 < len(identity) <= 4096
            and (key, identity) not in seen,
            "invalid multipart upload identity",
        )
        seen.add((key, identity))
    return rows


def _parts(client, bucket, key, identity):
    value = client.list_parts(Bucket=bucket, Key=key, UploadId=identity, MaxParts=100)
    _require(value.get("IsTruncated", False) is False, "multipart parts exceed budget")
    rows = value.get("Parts", [])
    _require(isinstance(rows, list) and len(rows) <= 100, "invalid multipart parts")
    found = {}
    for row in rows:
        number, size, etag = row.get("PartNumber"), row.get("Size"), row.get("ETag")
        _require(
            type(number) is int
            and 1 <= number <= 100
            and number not in found
            and type(size) is int
            and 0 < size <= MAX_CIPHERTEXT_BYTES
            and isinstance(etag, str)
            and 0 < len(etag) <= 256,
            "invalid multipart part identity",
        )
        found[number] = row
    _require(
        sum(row["Size"] for row in rows) <= MAX_CIPHERTEXT_BYTES, "multipart bytes exceed budget"
    )
    return found


def _complete(client, **arguments):
    meta = getattr(client, "meta", None)
    model = getattr(meta, "service_model", None)
    if (
        model is None
        or "IfNoneMatch" in model.operation_model("CompleteMultipartUpload").input_shape.members
    ):
        return client.complete_multipart_upload(**arguments, IfNoneMatch="*")
    event, identity = "before-sign.s3.CompleteMultipartUpload", "backup-create-only-completion"

    def conditional(request, **kwargs):
        request.headers["If-None-Match"] = "*"

    meta.events.register(event, conditional, unique_id=identity)
    try:
        return client.complete_multipart_upload(**arguments)
    finally:
        meta.events.unregister(event, unique_id=identity)


def _stored(client, bucket, key, ciphertext):
    try:
        _read_matches(client, bucket, key, ciphertext)
    except ClientError as error:
        if error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404:
            return False
        raise
    return True


def _transfer(db, client, bucket, record, ciphertext, max_bytes):
    key, snapshot = record["ciphertext_key"], record["snapshot_id"]
    endpoint = getattr(getattr(client, "meta", None), "endpoint_url", None)
    _require(isinstance(endpoint, str) and bool(endpoint), "multipart target identity missing")
    descriptor = json.dumps(
        {"bucket": bucket, "endpoint": endpoint, "record": record, "part_bytes": PART_BYTES},
        sort_keys=True,
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS multipart_transfers (snapshot_id TEXT PRIMARY KEY, "
        "descriptor TEXT NOT NULL, phase TEXT NOT NULL, upload_id TEXT)"
    )
    db.execute(
        "INSERT OR IGNORE INTO multipart_transfers VALUES (?,?,'planned',NULL)",
        (snapshot, descriptor),
    )
    db.commit()
    old, phase, upload_id = db.execute(
        "SELECT descriptor,phase,upload_id FROM multipart_transfers WHERE snapshot_id=?",
        (snapshot,),
    ).fetchone()
    _require(
        old == descriptor
        and phase in {"planned", "creating", "uploading", "completing", "verified"},
        "multipart journal identity changed",
    )

    def save(state, identity):
        db.execute(
            "UPDATE multipart_transfers SET phase=?,upload_id=? "
            "WHERE snapshot_id=? AND descriptor=?",
            (state, identity, snapshot, descriptor),
        )
        db.commit()

    if _stored(client, bucket, key, ciphertext):
        candidates = _uploads(client, bucket) if phase != "verified" else []
        matching = [row for row in candidates if row["Key"] == key]
        _require(len(matching) <= 1, "ambiguous multipart uploads")
        if matching:
            _require(upload_id in (None, matching[0]["UploadId"]), "multipart identity changed")
            client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=matching[0]["UploadId"])
        save("verified", None)
        return
    _require(phase != "verified", "verified ciphertext is missing")
    uploads = _uploads(client, bucket)
    matching = [row for row in uploads if row["Key"] == key]
    _require(len(matching) <= 1, "ambiguous multipart uploads")
    objects = inventory(client=client, bucket=bucket, prefix=PREFIX)
    expected = {
        key: len(ciphertext),
        PREFIX + snapshot + ".complete.json": len(marker_bytes(record)),
    }
    for name, size in expected.items():
        _require(
            name not in objects or objects[name]["bytes"] == size,
            "existing publication size differs",
        )
    reserved = 0
    for row in uploads:
        if row["Key"] != key:
            _parts(client, bucket, row["Key"], row["UploadId"])
            # Unknown/incomplete peers remain untouched; reserve their full cap,
            # not just currently visible parts. The namespace has one writer.
            reserved += MAX_CIPHERTEXT_BYTES
    total = sum(row["bytes"] for row in objects.values()) + reserved
    total += sum(size for name, size in expected.items() if name not in objects)
    _require(
        type(max_bytes) is int and 1 <= max_bytes <= DEFAULT_MAX_BYTES and total <= max_bytes,
        "multipart remote byte cap reached",
    )
    if phase == "planned":
        _require(not matching, "unclaimed multipart upload already exists")
        save("creating", None)
        response = client.create_multipart_upload(
            Bucket=bucket, Key=key, ContentType="application/octet-stream"
        )
        upload_id = response.get("UploadId")
        _require(
            isinstance(upload_id, str) and 0 < len(upload_id) <= 4096,
            "multipart create identity missing",
        )
        save("uploading", upload_id)
    else:
        # Some S3-compatible listings omit an active upload. A durably saved
        # identity can still be verified directly through ListParts; never
        # replace it or infer a fresh identity from an empty listing.
        if upload_id is None:
            _require(len(matching) == 1, "multipart creation remains unresolved")
            upload_id = matching[0]["UploadId"]
        elif matching:
            _require(upload_id == matching[0]["UploadId"], "multipart identity changed")
        save("uploading", upload_id)
    parts = _parts(client, bucket, key, upload_id)
    chunks = {
        i + 1: ciphertext[start : start + PART_BYTES]
        for i, start in enumerate(range(0, len(ciphertext), PART_BYTES))
    }
    _require(set(parts) <= set(chunks), "unexpected uploaded part")
    for number, row in parts.items():
        _require(
            row["Size"] == len(chunks[number]) and row["ETag"] == _etag(chunks[number]),
            "uploaded part differs from sealed ciphertext",
        )

    def send(number):
        data = chunks[number]
        response = client.upload_part(
            Bucket=bucket,
            Key=key,
            UploadId=upload_id,
            PartNumber=number,
            Body=data,
            ContentMD5=base64.b64encode(hashlib.md5(data, usedforsecurity=False).digest()).decode(),
        )
        _require(response.get("ETag") == _etag(data), "uploaded part checksum differs")

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(send, number) for number in chunks if number not in parts]
        for future in futures:
            future.result()
    save("completing", upload_id)
    _complete(
        client,
        Bucket=bucket,
        Key=key,
        UploadId=upload_id,
        MultipartUpload={
            "Parts": [
                {"PartNumber": number, "ETag": _etag(data)} for number, data in chunks.items()
            ]
        },
    )
    _read_matches(client, bucket, key, ciphertext)
    save("verified", None)


def upload_ciphertext(
    *, client, bucket, record, ciphertext, journal_path, max_bytes=DEFAULT_MAX_BYTES
):
    """Transfer only; marker publication stays with the existing publisher.

    Client timeouts/retries must be bounded. A failed call leaves durable state
    for the same sealed snapshot and never selects a replacement snapshot ID.
    """
    try:
        expected = snapshot_record(
            bucket=bucket,
            prefix=PREFIX,
            snapshot_id=record["snapshot_id"],
            ciphertext=ciphertext,
            captured_at=record["captured_at"],
        )
        _require(record == expected, "multipart input record differs")
        path = Path(journal_path)
        _require(
            path.name == "runtime.sqlite" and path.is_file() and not path.is_symlink(),
            "existing runtime journal required",
        )
        with contextlib.closing(sqlite3.connect(path, timeout=5)) as db:
            db.execute("PRAGMA synchronous=FULL")
            attempt = db.execute(
                "SELECT stage,captured_at,ciphertext_sha256,ciphertext_bytes "
                "FROM attempts WHERE id=?",
                (record["snapshot_id"],),
            ).fetchone()
            _require(
                attempt is not None
                and attempt[0] in {"sealed", "succeeded"}
                and tuple(attempt[1:])
                == (record["captured_at"], record["ciphertext_sha256"], record["ciphertext_bytes"]),
                "multipart source is not the sealed runtime attempt",
            )
            _transfer(db, client, bucket, record, ciphertext, max_bytes)
    except PublicationError:
        raise
    except Exception:
        raise PublicationError("multipart publication remains unconfirmed") from None
