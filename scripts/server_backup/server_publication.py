"""Immutable S3 ciphertext publication; completion requires full readback."""

import datetime
import hashlib
import json
import re
import secrets

from botocore.exceptions import ClientError, ConnectionError, HTTPClientError

MAX_CIPHERTEXT_BYTES = 64 * 1024 * 1024


class PublicationError(RuntimeError):
    """Safe operational failure without target credentials or response body."""


def _read_matches(client, bucket, key, expected):
    response = client.get_object(Bucket=bucket, Key=key)
    body = response["Body"]
    try:
        if response.get("ContentLength") != len(expected):
            raise PublicationError("stored ciphertext or marker length differs")
        digest = hashlib.sha256()
        count = 0
        while True:
            block = body.read(min(1024 * 1024, len(expected) - count + 1))
            if not block:
                break
            count += len(block)
            if count > len(expected):
                raise PublicationError("stored object exceeds expected length")
            digest.update(block)
        if count != len(expected) or digest.digest() != hashlib.sha256(expected).digest():
            raise PublicationError("stored object failed complete readback")
    finally:
        body.close()


def _put_immutable(client, bucket, key, data, content_type):
    try:
        meta = getattr(client, "meta", None)
        modeled = (
            meta is None
            or "IfNoneMatch" in meta.service_model.operation_model("PutObject").input_shape.members
        )
        if modeled:
            client.put_object(
                Bucket=bucket, Key=key, Body=data, IfNoneMatch="*", ContentType=content_type
            )
        else:
            # Older botocore models reject the keyword before sending. Add the
            # same HTTP precondition before signing; never send an unconditional PUT.
            event = "before-sign.s3.PutObject"
            identity = "docagent-conditional-put-" + secrets.token_hex(8)

            def conditional_header(request, **kwargs):
                request.headers["If-None-Match"] = "*"

            meta.events.register(event, conditional_header, unique_id=identity)
            try:
                client.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)
            finally:
                meta.events.unregister(event, unique_id=identity)
    except ClientError as error:
        status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status != 412 and not (isinstance(status, int) and 500 <= status < 600):
            raise
        # Existing keys and ambiguous server failures require matching every byte.
    except (ConnectionError, HTTPClientError):
        # R2 can finish PUT, or reject an existing key, before the SDK notices
        # a TLS disconnect. Resolve that uncertainty by reading the same key;
        # missing, incomplete or different bytes still prevent completion.
        pass
    _read_matches(client, bucket, key, data)


def snapshot_record(*, bucket, prefix, snapshot_id, ciphertext, captured_at):
    if not isinstance(bucket, str) or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
        raise PublicationError("invalid backup bucket")
    if prefix != "operations-recovery/v1/":
        raise PublicationError("unapproved backup prefix")
    if not re.fullmatch(r"snapshot-[a-z0-9-]{1,80}", snapshot_id):
        raise PublicationError("invalid snapshot identity")
    if (
        not isinstance(ciphertext, bytes)
        or not 32 <= len(ciphertext) <= MAX_CIPHERTEXT_BYTES
        or not ciphertext.startswith(b"age-encryption.org/v1\n")
    ):
        raise PublicationError("invalid or over-budget age ciphertext")
    try:
        source_time = datetime.datetime.fromisoformat(captured_at)
        if source_time.utcoffset() != datetime.timedelta(0):
            raise ValueError("source time must be UTC")
    except (TypeError, ValueError):
        raise PublicationError("invalid source snapshot timestamp") from None
    key = prefix + snapshot_id + ".tar.age"
    record = {
        "schema_version": 1,
        "snapshot_id": snapshot_id,
        "captured_at": captured_at,
        "ciphertext_key": key,
        "ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(),
        "ciphertext_bytes": len(ciphertext),
        "status": "ciphertext_upload_readback_verified",
        "actual_restore_verified": False,
    }
    return record


def marker_bytes(record):
    return (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()


def publish_snapshot(
    *,
    client,
    bucket,
    prefix,
    snapshot_id,
    ciphertext,
    captured_at,
    multipart_journal=None,
    max_bytes=3 * 1024**3,
):
    record = snapshot_record(
        bucket=bucket,
        prefix=prefix,
        snapshot_id=snapshot_id,
        ciphertext=ciphertext,
        captured_at=captured_at,
    )
    key = record["ciphertext_key"]
    marker = marker_bytes(record)
    try:
        if multipart_journal is None:
            _put_immutable(client, bucket, key, ciphertext, "application/octet-stream")
        else:
            from .multipart_publication import upload_ciphertext

            upload_ciphertext(
                client=client,
                bucket=bucket,
                record=record,
                ciphertext=ciphertext,
                journal_path=multipart_journal,
                max_bytes=max_bytes,
            )
        _put_immutable(
            client, bucket, prefix + snapshot_id + ".complete.json", marker, "application/json"
        )
    except PublicationError:
        raise
    except Exception:
        # Unknown PUT results remain unknown; no blind new snapshot/key retry here.
        raise PublicationError("backup publication did not complete") from None
    return record
