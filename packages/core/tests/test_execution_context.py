from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from enterprise_doc_core.agents import (
    ExecutionContextExpired,
    ExecutionContextInvalidSignature,
    ExecutionContextMalformed,
    ExecutionContextNotYetValid,
    ExecutionContextVersionUnsupported,
    SignedExecutionContext,
    ToolCapability,
    sign_execution_context,
    verify_execution_context,
)

SECRET = "s" * 40
NOW = datetime(2026, 7, 19, 10, 0, tzinfo=UTC)


def _context(**overrides: object) -> SignedExecutionContext:
    values: dict[str, object] = {
        "tenant_id": UUID("00000000-0000-0000-0000-000000000001"),
        "actor_id": UUID("00000000-0000-0000-0000-000000000002"),
        "run_id": UUID("00000000-0000-0000-0000-000000000003"),
        "execution_id": UUID("00000000-0000-0000-0000-000000000004"),
        "capabilities": (ToolCapability.READ_EVIDENCE,),
        "target_document_version_id": UUID("00000000-0000-0000-0000-000000000005"),
        "issued_at": NOW - timedelta(seconds=1),
        "expires_at": NOW + timedelta(minutes=5),
        "nonce": "nonce_1234567890",
    }
    values.update(overrides)
    return SignedExecutionContext.model_validate(values)


def test_execution_context_round_trips_with_canonical_hmac() -> None:
    context = _context()
    token = sign_execution_context(context, SECRET)
    verified = verify_execution_context(token, SECRET, now=NOW)

    assert verified == context
    assert token.count(".") == 2
    assert SECRET not in token


def test_execution_context_signature_and_payload_tampering_fail_closed() -> None:
    token = sign_execution_context(_context(), SECRET)
    payload, signature = token.rsplit(".", maxsplit=1)

    with pytest.raises(ExecutionContextInvalidSignature):
        verify_execution_context(f"{payload[:-1]}A.{signature}", SECRET, now=NOW)

    with pytest.raises(ExecutionContextInvalidSignature):
        verify_execution_context(f"{payload}.{signature[:-1]}A", SECRET, now=NOW)


@pytest.mark.parametrize(
    ("token_mutator", "error_type"),
    [
        (lambda token: token.replace("v1.", "v2.", 1), ExecutionContextVersionUnsupported),
        (lambda token: token + ".extra", ExecutionContextMalformed),
        (lambda token: "v1.!invalid.signature", ExecutionContextMalformed),
    ],
)
def test_execution_context_wire_errors_are_stable(
    token_mutator: Callable[[str], str],
    error_type: type[Exception],
) -> None:
    token = sign_execution_context(_context(), SECRET)
    with pytest.raises(error_type):
        verify_execution_context(token_mutator(token), SECRET, now=NOW)


def test_execution_context_expiry_and_future_time_are_checked() -> None:
    expired = sign_execution_context(
        _context(
            issued_at=NOW - timedelta(minutes=1),
            expires_at=NOW - timedelta(seconds=1),
        ),
        SECRET,
    )
    with pytest.raises(ExecutionContextExpired):
        verify_execution_context(expired, SECRET, now=NOW)

    future = sign_execution_context(
        _context(issued_at=NOW + timedelta(seconds=10)),
        SECRET,
    )
    with pytest.raises(ExecutionContextNotYetValid):
        verify_execution_context(future, SECRET, now=NOW)


def test_publish_context_requires_approval_binding_and_unique_capabilities() -> None:
    with pytest.raises(ValidationError):
        _context(capabilities=(ToolCapability.PUBLISH,))
    with pytest.raises(ValidationError):
        _context(capabilities=(ToolCapability.READ_EVIDENCE, ToolCapability.READ_EVIDENCE))
