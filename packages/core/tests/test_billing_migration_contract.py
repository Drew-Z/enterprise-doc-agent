from __future__ import annotations

from pathlib import Path

MIGRATION = (
    Path(__file__).parents[1]
    / "src/enterprise_doc_core/db/migrations/versions/20260914_0026_entitlements_usage.py"
)


def test_entitlements_usage_migration_is_additive_after_membership_invitations() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "20260914_0026"' in source
    assert 'down_revision = "20260914_0025"' in source
    for table in ("tenant_entitlements", "usage_reservations", "usage_events"):
        assert f'"{table}"' in source
    assert 'op.drop_table("usage_events")' in source
    assert 'op.drop_table("usage_reservations")' in source
