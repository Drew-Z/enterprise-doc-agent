from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from enterprise_doc_core.billing.models import TenantEntitlement, UsageEvent, UsageReservation
from enterprise_doc_core.db.metadata import REGISTERED_MODELS


def _constraint_names(model: type[object]) -> set[str]:
    return {
        constraint.name for constraint in model.__table__.constraints if constraint.name is not None
    }


def test_billing_models_are_registered_and_tenant_scoped() -> None:
    assert {TenantEntitlement, UsageReservation, UsageEvent} <= set(REGISTERED_MODELS)
    for model in (TenantEntitlement, UsageReservation, UsageEvent):
        assert any(
            isinstance(item, UniqueConstraint) and "tenant_id" in {c.name for c in item.columns}
            for item in model.__table__.constraints
        )

    reservation_fks = [
        item
        for item in UsageReservation.__table__.constraints
        if isinstance(item, ForeignKeyConstraint)
    ]
    event_fks = [
        item for item in UsageEvent.__table__.constraints if isinstance(item, ForeignKeyConstraint)
    ]
    assert any(
        len(item.column_keys) == 2 and item.column_keys == ["tenant_id", "entitlement_id"]
        for item in reservation_fks
    )
    assert any(
        len(item.column_keys) == 2 and item.column_keys == ["tenant_id", "reservation_id"]
        for item in event_fks
    )


def test_billing_models_expose_state_and_non_negative_checks() -> None:
    reservation_checks = _constraint_names(UsageReservation)
    event_checks = _constraint_names(UsageEvent)
    entitlement_checks = _constraint_names(TenantEntitlement)
    assert any(name.endswith("reservation_state_valid") for name in reservation_checks)
    assert any(name.endswith("provider_request_metric") for name in reservation_checks)
    assert any(name.endswith("usage_event_type_valid") for name in event_checks)
    assert any(name.endswith("usage_event_cost_non_negative") for name in event_checks)
    assert any(
        name.endswith("provider_request_counters_non_negative") for name in entitlement_checks
    )
    assert any(isinstance(item, CheckConstraint) for item in UsageEvent.__table__.constraints)
