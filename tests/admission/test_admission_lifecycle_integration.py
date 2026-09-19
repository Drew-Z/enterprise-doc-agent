from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from enterprise_doc_core.admission.contracts import (
    AdmissionGrantRequest,
    PlatformAdmissionOperator,
    prepare_admission_credential,
)
from enterprise_doc_core.admission.errors import AdmissionConflict, AdmissionForbidden
from enterprise_doc_core.admission.models import TenantAdmissionEvent, TenantAdmissionGrant
from enterprise_doc_core.admission.service import TenantAdmissionService
from tests.admission.conftest import AdmissionDatabase

pytestmark = pytest.mark.integration
ISSUER = "https://identity.example.test"
OPERATOR = PlatformAdmissionOperator(operator_id="test-operator", reason="Admission verification")


def grant_request(**overrides: object) -> AdmissionGrantRequest:
    return AdmissionGrantRequest.model_validate(
        {
            "recipient_email": "owner@example.test",
            "issuer": ISSUER,
            "expires_at": datetime.now(UTC) + timedelta(hours=1),
            "quota_bytes": 4096,
            "seat_limit": 3,
            **overrides,
        }
    )


def service_for(database: AdmissionDatabase) -> TenantAdmissionService:
    return TenantAdmissionService(
        session_factory=database.sessions, trusted_issuers=frozenset({ISSUER})
    )


async def test_issue_show_revoke_is_audited_and_idempotent(admission_db: AdmissionDatabase) -> None:
    service = service_for(admission_db)
    credential = prepare_admission_credential()
    request = grant_request()
    issued = await service.issue(operator=OPERATOR, request=request, credential=credential)
    repeated = await service.issue(operator=OPERATOR, request=request, credential=credential)
    assert repeated == issued
    assert issued.state == "pending" and issued.tenant_id is None
    assert credential.token.get_secret_value() not in str(asdict(issued))
    async with admission_db.sessions() as session:
        grant = await session.get(TenantAdmissionGrant, credential.grant_id)
        assert grant is not None
        assert grant.token_digest != credential.token.get_secret_value()
        assert len(grant.token_digest) == 64
        assert await session.scalar(select(func.count()).select_from(TenantAdmissionEvent)) == 1
    revoked = await service.revoke(operator=OPERATOR, grant_id=credential.grant_id)
    assert revoked.state == "revoked" and revoked.revoked_at is not None
    assert await service.revoke(operator=OPERATOR, grant_id=credential.grant_id) == revoked
    assert await service.show(operator=OPERATOR, grant_id=credential.grant_id) == revoked
    async with admission_db.sessions() as session:
        events = (
            await session.scalars(
                select(TenantAdmissionEvent).order_by(TenantAdmissionEvent.occurred_at)
            )
        ).all()
        assert [event.action for event in events] == ["issued", "revoked"]
        assert all(event.operator_id == OPERATOR.operator_id for event in events)
        assert all(event.reason == OPERATOR.reason for event in events)


async def test_issue_refuses_changed_request_and_tenant_owner(
    admission_db: AdmissionDatabase,
) -> None:
    service = service_for(admission_db)
    credential = prepare_admission_credential()
    request = grant_request()
    await service.issue(operator=OPERATOR, request=request, credential=credential)
    with pytest.raises(AdmissionConflict):
        await service.issue(
            operator=OPERATOR,
            request=request.model_copy(update={"seat_limit": 4}),
            credential=credential,
        )
    with pytest.raises(AdmissionForbidden):
        await service.show(operator="owner", grant_id=credential.grant_id)  # type: ignore[arg-type]
