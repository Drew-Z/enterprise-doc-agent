from __future__ import annotations

from uuid import uuid4

import pytest

from enterprise_doc_core.agents import (
    ApprovalInputInvalid,
    ApprovalPrincipalForbidden,
    ApprovalService,
    DecideApprovalInput,
    approval_decision_fingerprint,
)
from enterprise_doc_core.telemetry import MetricsRuntime


def _request(**overrides: object) -> DecideApprovalInput:
    values: dict[str, object] = {
        "decision": "approved",
        "operation": "publish_artifact",
        "target_resource_type": "agent_artifact",
        "target_resource_id": uuid4(),
        "target_document_version_id": uuid4(),
        "target_fingerprint": "a" * 64,
        "comment": "Reviewed",
    }
    values.update(overrides)
    return DecideApprovalInput(**values)  # type: ignore[arg-type]


def test_approval_decision_fingerprint_is_stable_and_exact_target_bound() -> None:
    approval_id = uuid4()
    request = _request()

    first = approval_decision_fingerprint(approval_id=approval_id, request=request)
    replay = approval_decision_fingerprint(approval_id=approval_id, request=request)
    changed_decision = approval_decision_fingerprint(
        approval_id=approval_id,
        request=_request(
            decision="rejected",
            target_resource_id=request.target_resource_id,
            target_document_version_id=request.target_document_version_id,
        ),
    )
    changed_target = approval_decision_fingerprint(
        approval_id=approval_id,
        request=_request(target_document_version_id=request.target_document_version_id),
    )

    assert first == replay
    assert first != changed_decision
    assert first != changed_target
    assert len(first) == 64


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("idempotency_key", "decision_input"),
    [
        ("", _request()),
        ("decision-1", _request(decision="unsupported")),
        ("decision-1", _request(operation="other")),
        ("decision-1", _request(target_fingerprint="not-a-hash")),
        ("decision-1", _request(comment="x" * 1001)),
    ],
)
async def test_approval_service_rejects_invalid_input_before_database_access(
    idempotency_key: str,
    decision_input: DecideApprovalInput,
) -> None:
    service = ApprovalService(session_factory=None)  # type: ignore[arg-type]

    with pytest.raises(ApprovalInputInvalid):
        await service.decide(
            tenant_id=uuid4(),
            actor_id=uuid4(),
            approval_id=uuid4(),
            idempotency_key=idempotency_key,
            request=decision_input,
        )


async def test_approval_service_records_success_and_forbidden_boundaries() -> None:
    metrics = MetricsRuntime.create()
    service = ApprovalService(
        session_factory=None,  # type: ignore[arg-type]
        metrics=metrics,
    )

    async def succeed(**_: object) -> object:
        return object()

    service._decide = succeed  # type: ignore[method-assign]
    await service.decide(
        tenant_id=uuid4(),
        actor_id=uuid4(),
        approval_id=uuid4(),
        idempotency_key="decision-1",
        request=_request(),
    )

    async def forbid(**_: object) -> object:
        raise ApprovalPrincipalForbidden()

    service._decide = forbid  # type: ignore[method-assign]
    with pytest.raises(ApprovalPrincipalForbidden):
        await service.decide(
            tenant_id=uuid4(),
            actor_id=uuid4(),
            approval_id=uuid4(),
            idempotency_key="decision-2",
            request=_request(),
        )

    rendered = metrics.render().decode("utf-8")
    assert 'boundary="approval",operation="decide",result="success"' in rendered
    assert 'boundary="approval",operation="decide",result="forbidden"' in rendered
