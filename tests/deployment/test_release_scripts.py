from __future__ import annotations

import pytest
from scripts.backup_database import normalize_postgres_url
from scripts.restore_database import restore_command, validate_restore_target
from scripts.rollback_release import rollback_commands


def test_database_url_normalization_removes_driver_suffix() -> None:
    assert (
        normalize_postgres_url("postgresql+psycopg://user:pass@db:5432/app")
        == "postgresql://user:pass@db:5432/app"
    )


def test_restore_command_is_single_transaction_and_explicitly_destructive() -> None:
    command = restore_command(
        database_url="postgresql+psycopg://user:pass@db:5432/app",
        backup=__import__("pathlib").Path("backup.dump"),
    )
    assert "--single-transaction" in command
    assert "--clean" in command
    assert "--if-exists" in command


def test_rollback_commands_are_explicit_and_ordered() -> None:
    commands = rollback_commands(
        namespace="enterprise-doc-agent-staging",
        revisions={"enterprise-doc-api": 42, "enterprise-doc-worker": 7},
    )
    assert commands == [
        [
            "kubectl",
            "-n",
            "enterprise-doc-agent-staging",
            "rollout",
            "undo",
            "deployment/enterprise-doc-api",
            "--to-revision=42",
        ],
        [
            "kubectl",
            "-n",
            "enterprise-doc-agent-staging",
            "rollout",
            "status",
            "deployment/enterprise-doc-api",
            "--timeout=300s",
        ],
        [
            "kubectl",
            "-n",
            "enterprise-doc-agent-staging",
            "rollout",
            "undo",
            "deployment/enterprise-doc-worker",
            "--to-revision=7",
        ],
        [
            "kubectl",
            "-n",
            "enterprise-doc-agent-staging",
            "rollout",
            "status",
            "deployment/enterprise-doc-worker",
            "--timeout=300s",
        ],
    ]


def test_rollback_rejects_ambiguous_or_unsafe_targets() -> None:
    with pytest.raises(ValueError):
        rollback_commands(
            namespace="enterprise-doc-agent-staging",
            revisions={"unknown": 1},
        )
    with pytest.raises(ValueError):
        rollback_commands(
            namespace="enterprise-doc-agent",
            revisions={"enterprise-doc-api": 1},
        )


def test_restore_target_requires_matching_host_and_production_confirmation() -> None:
    assert (
        validate_restore_target(
            database_url="postgresql+psycopg://user:pass@staging-db:5432/app",
            expected_host="staging-db",
            environment="staging",
            production_confirmation=None,
        )
        == "postgresql://user:pass@staging-db:5432/app"
    )
    with pytest.raises(ValueError):
        validate_restore_target(
            database_url="postgresql://user:pass@other-db:5432/app",
            expected_host="staging-db",
            environment="staging",
            production_confirmation=None,
        )
    with pytest.raises(ValueError):
        validate_restore_target(
            database_url="postgresql://user:pass@prod-db:5432/app",
            expected_host="prod-db",
            environment="production",
            production_confirmation=None,
        )
