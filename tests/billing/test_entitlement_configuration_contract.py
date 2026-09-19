from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from enterprise_doc_core.billing.administration_contracts import EntitlementConfiguration


@pytest.mark.parametrize(
    "changes",
    [
        {"provider_request_limit": -1},
        {"provider_request_limit": None},
        {"provider_request_limit": True},
        {"provider_request_limit": 2**63},
        {"expected_version": -1},
        {"expected_version": True},
        {"expected_version": 2**31 - 1},
        {"period_start": "2026-09-14T00:00:00"},
        {"period_end": "2026-09-14T00:00:00+00:00"},
        {"plan_code": ""},
        {"plan_code": "trial\n"},
        {"extra": "unrecognized"},
    ],
)
def test_entitlement_configuration_rejects_ambiguous_or_unbounded_values(changes) -> None:
    values = {
        "entitlement_id": uuid4(),
        "expected_version": 0,
        "plan_code": "trial",
        "period_start": datetime(2026, 9, 14, tzinfo=UTC),
        "period_end": datetime(2026, 9, 15, tzinfo=UTC),
        "provider_request_limit": 2,
    }
    with pytest.raises(ValidationError):
        EntitlementConfiguration.model_validate({**values, **changes})


def test_period_is_normalized_to_utc_and_explicit_zero_is_valid() -> None:
    start = datetime(2026, 9, 14, 8, tzinfo=timezone(timedelta(hours=8)))
    configuration = EntitlementConfiguration(
        entitlement_id=uuid4(),
        expected_version=0,
        plan_code="trial",
        period_start=start,
        period_end=start + timedelta(days=1),
        provider_request_limit=0,
    )
    assert configuration.period_start.isoformat() == "2026-09-14T00:00:00+00:00"
    assert configuration.provider_request_limit == 0
