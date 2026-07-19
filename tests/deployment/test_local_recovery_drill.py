from __future__ import annotations

from pathlib import Path

import pytest
from scripts.local_recovery_drill import (
    LocalRecoveryDrillError,
    compose_command,
    validate_database_names,
)


def test_recovery_drill_requires_isolated_prefixed_database() -> None:
    validate_database_names("enterprise_doc", "enterprise_doc_restore_drill")
    with pytest.raises(LocalRecoveryDrillError, match="differ"):
        validate_database_names("enterprise_doc", "enterprise_doc")
    with pytest.raises(LocalRecoveryDrillError, match="start with"):
        validate_database_names("enterprise_doc", "other_restore")
    with pytest.raises(LocalRecoveryDrillError, match="invalid"):
        validate_database_names("enterprise-doc", "enterprise_doc_restore_drill")


def test_compose_command_is_explicit_and_non_shell() -> None:
    assert compose_command(Path("infra/compose/docker-compose.yml"), "ps", "postgres") == [
        "docker",
        "compose",
        "-f",
        "infra/compose/docker-compose.yml",
        "ps",
        "postgres",
    ]
