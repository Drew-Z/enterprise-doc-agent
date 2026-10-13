from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from enterprise_doc_core.billing import EntitlementUsageService, UsageError
from enterprise_doc_core.billing.models import UsageEvent


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def all(self) -> list[Any]:
        return self.rows


class _Session:
    def __init__(self, scalar_values: list[Any], rows: list[Any] | None = None) -> None:
        self.scalar_values = list(scalar_values)
        self.rows = rows or []
        self.added: list[Any] = []

    async def scalar(self, _statement: Any) -> Any:
        return self.scalar_values.pop(0)

    async def execute(self, _statement: Any) -> None:
        return None

    async def scalars(self, _statement: Any) -> _Result:
        return _Result(self.rows)

    def add(self, value: Any) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        return None


class _Transaction:
    def __init__(self, session: _Session) -> None:
        self.session = session

    async def __aenter__(self) -> _Session:
        return self.session

    async def __aexit__(self, *_: Any) -> None:
        return None


class _Factory:
    def __init__(self, session: _Session) -> None:
        self.session = session

    def begin(self) -> _Transaction:
        return _Transaction(self.session)


def test_usage_value_normalization_preserves_unknowns_and_rejects_invalid_costs() -> None:
    values = EntitlementUsageService._usage_values(
        {"prompt_tokens": 4, "completion_tokens": 6}, None
    )
    assert values == {
        "input_tokens": 4,
        "output_tokens": 6,
        "total_tokens": None,
        "estimated_cost": None,
    }
    with pytest.raises(UsageError, match="usage_invalid_cost"):
        EntitlementUsageService._usage_values({}, "NaN")
    with pytest.raises(UsageError, match="usage_invalid_cost"):
        EntitlementUsageService._usage_values({}, -1)


def test_settlement_replay_detects_explicit_payload_conflicts() -> None:
    event = UsageEvent(
        tenant_id=uuid4(),
        entitlement_id=uuid4(),
        reservation_id=uuid4(),
        operation_id=uuid4(),
        event_type="consume",
        quantity=1,
        provider="provider-a",
        model="model-a",
        total_tokens=5,
        source="presales",
        occurred_at=datetime(2026, 9, 14, tzinfo=UTC),
    )
    assert EntitlementUsageService._settlement_matches(
        event,
        provider="provider-a",
        model="model-a",
        usage={"total_tokens": 5},
        currency=None,
        estimated_cost=None,
        pricing_version=None,
        source="presales",
    )
    assert not EntitlementUsageService._settlement_matches(
        event,
        provider="provider-b",
        model="model-a",
        usage={"total_tokens": 5},
        currency=None,
        estimated_cost=None,
        pricing_version=None,
        source="presales",
    )


@pytest.mark.asyncio
async def test_legacy_tenant_does_not_create_reservation() -> None:
    tenant_id, operation_id = uuid4(), uuid4()
    session = _Session([tenant_id, None, None, None], [])
    service = EntitlementUsageService(session_factory=_Factory(session))  # type: ignore[arg-type]
    result = await service.reserve_provider_request(tenant_id=tenant_id, operation_id=operation_id)
    assert result.status == "legacy"
    assert result.ledgered is False
    assert session.added == []
