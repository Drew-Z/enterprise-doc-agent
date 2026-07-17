from __future__ import annotations

import json
import logging
from io import StringIO

from enterprise_doc_core.context import (
    PrincipalContext,
    RequestContext,
    enrich_request_principal,
    get_request_context,
    reset_request_context,
    set_request_context,
)
from enterprise_doc_core.logging import JsonFormatter


def test_principal_enrichment_preserves_request_identifiers() -> None:
    token = set_request_context(
        RequestContext(request_id="request-1", correlation_id="correlation-1")
    )
    principal = PrincipalContext(tenant_id="tenant-1", actor_id="actor-1", role="owner")
    try:
        enrich_request_principal(principal)

        assert get_request_context() == RequestContext(
            request_id="request-1",
            correlation_id="correlation-1",
            principal=principal,
        )
    finally:
        reset_request_context(token)

    assert get_request_context() is None


def test_json_logging_adds_only_resolved_principal_fields() -> None:
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(service="api", environment="test"))
    logger = logging.getLogger("test-principal-logging")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    token = set_request_context(
        RequestContext(
            request_id="request-1",
            correlation_id="correlation-1",
            principal=PrincipalContext(
                tenant_id="tenant-1",
                actor_id="actor-1",
                role="member",
            ),
        )
    )
    try:
        logger.info(
            "authenticated_request",
            extra={
                "event_data": {
                    "authorization": "Bearer secret-token",
                    "jwt_token": "secret-token",
                }
            },
        )
    finally:
        reset_request_context(token)

    payload = json.loads(stream.getvalue())
    assert payload["tenant_id"] == "tenant-1"
    assert payload["actor_id"] == "actor-1"
    assert payload["authorization"] == "**********"
    assert payload["jwt_token"] == "**********"
    assert "secret-token" not in stream.getvalue()
    assert "role" not in payload
