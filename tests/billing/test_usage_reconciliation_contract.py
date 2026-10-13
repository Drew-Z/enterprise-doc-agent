from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from enterprise_doc_core.billing.reconciliation_contracts import UsageExportWindow


@pytest.mark.parametrize(
    "change",
    [
        {"start": "2026-09-01T00:00:00"},
        {"start": 1788220800},
        {"start": "1788220800"},
        {"start": 1788220800.0},
        {"end": "2026-09-01T00:00:00Z"},
        {"end": "2026-10-03T00:00:00Z"},
        {"limit_per_source": 0},
        {"limit_per_source": 5001},
        {"limit_per_source": True},
        {"limit_per_source": "2"},
        {"all_tenants": True},
    ],
)
def test_export_window_rejects_ambiguous_or_unbounded_input(change):
    with pytest.raises(ValidationError):
        UsageExportWindow(
            **{"start": "2026-09-01T00:00:00Z", "end": "2026-09-02T00:00:00Z", **change}
        )


def test_export_window_normalizes_offsets_and_allows_one_month():
    window = UsageExportWindow(start="2026-09-01T08:00:00+08:00", end="2026-10-02T00:00:00Z")
    assert window.start == datetime(2026, 9, 1, tzinfo=UTC)
    assert window.end - window.start == timedelta(days=31)
