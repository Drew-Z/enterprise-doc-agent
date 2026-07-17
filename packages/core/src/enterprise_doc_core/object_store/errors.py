from __future__ import annotations

from botocore.exceptions import BotoCoreError, ClientError


class ObjectStoreError(RuntimeError):
    code = "object_store_error"
    message = "The object-store operation failed."

    def __init__(self) -> None:
        super().__init__(self.message)


class ObjectStoreNotFound(ObjectStoreError):
    code = "object_not_found"
    message = "The requested object was not found."


class MultipartUploadNotFound(ObjectStoreError):
    code = "multipart_upload_not_found"
    message = "The multipart upload was not found."


class ObjectStoreChecksumMismatch(ObjectStoreError):
    code = "object_checksum_mismatch"
    message = "The object-store checksum validation failed."


class ObjectStoreUnavailable(ObjectStoreError):
    code = "object_store_unavailable"
    message = "The object store is temporarily unavailable."


class ObjectStoreRejected(ObjectStoreError):
    code = "object_store_rejected"
    message = "The object store rejected the operation."


class ObjectStoreProtocolError(ObjectStoreError):
    code = "object_store_protocol_error"
    message = "The object-store response violated the multipart contract."


def normalize_object_store_error(error: BotoCoreError | ClientError) -> ObjectStoreError:
    if not isinstance(error, ClientError):
        return ObjectStoreUnavailable()

    response_error = error.response.get("Error", {})
    code = str(response_error.get("Code", ""))
    status_code = int(error.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
    if code == "NoSuchUpload":
        return MultipartUploadNotFound()
    if code in {"NoSuchKey", "NotFound", "404"} or status_code == 404:
        return ObjectStoreNotFound()
    if code in {"BadDigest", "InvalidDigest", "XAmzContentSHA256Mismatch"}:
        return ObjectStoreChecksumMismatch()
    if code in {"SlowDown", "RequestTimeout", "ServiceUnavailable", "InternalError"} or (
        status_code >= 500
    ):
        return ObjectStoreUnavailable()
    return ObjectStoreRejected()
