from __future__ import annotations

from typing import Any

import boto3
from botocore.config import Config

from enterprise_doc_core.config import ObjectStoreSettings


def create_s3_client(
    settings: ObjectStoreSettings,
    *,
    endpoint_url: str,
) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=settings.access_key.get_secret_value(),
        aws_secret_access_key=settings.secret_key.get_secret_value(),
        aws_session_token=(
            settings.session_token.get_secret_value()
            if settings.session_token is not None
            else None
        ),
        region_name=settings.region,
        use_ssl=settings.secure,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
            connect_timeout=settings.connect_timeout_seconds,
            read_timeout=settings.read_timeout_seconds,
            max_pool_connections=settings.max_pool_connections,
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )
