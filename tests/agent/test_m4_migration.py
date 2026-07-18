from __future__ import annotations

import os
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    ROOT
    / "packages/core/src/enterprise_doc_core/db/migrations/versions"
    / "20260718_0009_agent_mcp_hitl.py"
)
DATABASE_URL = os.environ.get(
    "FOUNDATION_TEST_DATABASE_URL",
    "postgresql://enterprise_doc:enterprise_doc_local@127.0.0.1:5432/enterprise_doc",
)
AGENT_TABLES = (
    "agent_run_executions",
    "agent_run_events",
    "agent_run_evidence",
    "approval_requests",
    "tool_executions",
    "agent_artifacts",
    "agent_runs",
)


def _run_alembic(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "alembic", *arguments],
        cwd=ROOT,
        check=check,
        capture_output=True,
        text=True,
    )


def test_m4_migration_is_additive_after_m3_hardening() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision = "20260718_0009"' in source
    assert 'down_revision = "20260718_0008"' in source
    for table in AGENT_TABLES:
        assert f'"{table}"' in source
    assert "cannot downgrade while M4 Agent rows exist" in source


def _seed_foundation(cursor: psycopg.Cursor[tuple[object, ...]]) -> dict[str, UUID]:
    ids = {
        "tenant": uuid4(),
        "actor": uuid4(),
        "membership": uuid4(),
        "upload": uuid4(),
        "document": uuid4(),
        "version": uuid4(),
        "generation": uuid4(),
        "chunk": uuid4(),
        "job": uuid4(),
        "run": uuid4(),
        "event": uuid4(),
        "execution": uuid4(),
        "evidence": uuid4(),
        "artifact": uuid4(),
        "approval": uuid4(),
        "tool": uuid4(),
    }
    suffix = ids["tenant"].hex
    cursor.execute(
        "INSERT INTO tenants (id, name, slug, quota_bytes) VALUES (%s, %s, %s, %s)",
        (ids["tenant"], "M4 migration tenant", f"m4-migration-{suffix}", 1024 * 1024),
    )
    cursor.execute(
        "INSERT INTO users (id, email) VALUES (%s, %s)",
        (ids["actor"], f"m4-{suffix}@example.test"),
    )
    cursor.execute(
        "INSERT INTO memberships (id, tenant_id, user_id, role) VALUES (%s, %s, %s, 'owner')",
        (ids["membership"], ids["tenant"], ids["actor"]),
    )
    cursor.execute(
        """
        INSERT INTO upload_sessions (
            id, tenant_id, actor_id, pending_document_id, pending_version_id, status,
            idempotency_key, request_fingerprint, object_key, original_filename,
            extension, declared_media_type, size_bytes, declared_sha256,
            part_size_bytes, expected_part_count, reserved_bytes, expires_at
        ) VALUES (
            %s, %s, %s, %s, %s, 'completed', %s, %s, %s, 'contract.txt', '.txt',
            'text/plain', 8, %s, 5242880, 1, 0, now() + interval '1 hour'
        )
        """,
        (
            ids["upload"],
            ids["tenant"],
            ids["actor"],
            ids["document"],
            ids["version"],
            f"m4-upload-{suffix}",
            "a" * 64,
            f"{ids['tenant']}/m4/{ids['version']}/contract.txt",
            "b" * 64,
        ),
    )
    cursor.execute(
        "INSERT INTO documents (id, tenant_id, created_by, title) VALUES (%s, %s, %s, %s)",
        (ids["document"], ids["tenant"], ids["actor"], "M4 contract"),
    )
    cursor.execute(
        """
        INSERT INTO document_versions (
            id, tenant_id, document_id, upload_session_id, version_number, status,
            object_key, original_filename, declared_media_type, detected_media_type,
            size_bytes, declared_sha256, created_by
        ) VALUES (%s, %s, %s, %s, 1, 'ready', %s, 'contract.txt', 'text/plain',
                  'text/plain', 8, %s, %s)
        """,
        (
            ids["version"],
            ids["tenant"],
            ids["document"],
            ids["upload"],
            f"{ids['tenant']}/m4/{ids['version']}/ready-contract.txt",
            "b" * 64,
            ids["actor"],
        ),
    )
    cursor.execute(
        """
        INSERT INTO document_ingestion_generations (
            id, tenant_id, document_version_id, embedding_model, status, stage, active,
            chunk_count, embedded_count
        ) VALUES (%s, %s, %s, 'migration-fixture', 'succeeded', 'ready', true, 1, 1)
        """,
        (ids["generation"], ids["tenant"], ids["version"]),
    )
    cursor.execute(
        """
        INSERT INTO document_chunks (
            id, tenant_id, document_version_id, generation_id, chunk_index,
            start_offset, end_offset, normalized_text, content_sha256, search_vector,
            embedding
        ) VALUES (%s, %s, %s, %s, 0, 0, 8, 'evidence', %s,
                  to_tsvector('simple', 'evidence'), NULL)
        """,
        (ids["chunk"], ids["tenant"], ids["version"], ids["generation"], "c" * 64),
    )
    cursor.execute(
        """
        INSERT INTO jobs (
            id, tenant_id, actor_id, type, status, idempotency_key,
            request_fingerprint, payload
        ) VALUES (%s, %s, %s, 'agent.execute', 'pending', %s, %s, '{}'::json)
        """,
        (ids["job"], ids["tenant"], ids["actor"], f"m4-job-{suffix}", "d" * 64),
    )
    return ids


def _insert_valid_agent_rows(
    cursor: psycopg.Cursor[tuple[object, ...]],
    ids: dict[str, UUID],
) -> None:
    cursor.execute(
        """
        INSERT INTO agent_runs (
            id, tenant_id, actor_id, document_version_id, idempotency_key,
            request_fingerprint, task_type, input_text, publish_requested, status,
            graph_thread_id, graph_version, prompt_version, model_provider, model_name,
            tool_schema_version, index_generation_id
        ) VALUES (%s, %s, %s, %s, %s, %s, 'question_answer', 'Summarize payment',
                  true, 'pending', %s, 'm4.v1', 'm4.v1', 'deterministic',
                  'deterministic-grounded', 'm4.v1', %s)
        """,
        (
            ids["run"],
            ids["tenant"],
            ids["actor"],
            ids["version"],
            f"m4-run-{ids['run'].hex}",
            "e" * 64,
            str(ids["run"]),
            ids["generation"],
        ),
    )
    cursor.execute(
        """
        INSERT INTO agent_run_events (
            id, tenant_id, run_id, seq, event_type, event_version, public_payload
        ) VALUES (%s, %s, %s, 1, 'run.created', 1, '{}'::json)
        """,
        (ids["event"], ids["tenant"], ids["run"]),
    )
    cursor.execute(
        """
        INSERT INTO agent_run_executions (
            id, tenant_id, run_id, sequence, job_id, kind
        ) VALUES (%s, %s, %s, 0, %s, 'initial')
        """,
        (ids["execution"], ids["tenant"], ids["run"], ids["job"]),
    )
    cursor.execute(
        """
        INSERT INTO agent_run_evidence (
            id, tenant_id, run_id, chunk_id, document_version_id, generation_id,
            rank, rrf_score, content_sha256
        ) VALUES (%s, %s, %s, %s, %s, %s, 1, 0.032, %s)
        """,
        (
            ids["evidence"],
            ids["tenant"],
            ids["run"],
            ids["chunk"],
            ids["version"],
            ids["generation"],
            "c" * 64,
        ),
    )
    cursor.execute(
        """
        INSERT INTO agent_artifacts (
            id, tenant_id, run_id, source_document_version_id, kind, status,
            content_type, object_bucket, object_key, behavior_versions
        ) VALUES (%s, %s, %s, %s, 'grounded_answer', 'writing', 'application/json',
                  'artifacts', %s, '{}'::json)
        """,
        (
            ids["artifact"],
            ids["tenant"],
            ids["run"],
            ids["version"],
            f"{ids['tenant']}/agent/{ids['run']}/answer.json",
        ),
    )
    cursor.execute(
        """
        INSERT INTO approval_requests (
            id, tenant_id, run_id, requested_by_actor_id, operation,
            target_resource_type, target_resource_id, target_document_version_id,
            target_fingerprint, status, requested_at, expires_at
        ) VALUES (%s, %s, %s, %s, 'publish_artifact', 'agent_artifact', %s, %s,
                  %s, 'pending', now(), now() + interval '15 minutes')
        """,
        (
            ids["approval"],
            ids["tenant"],
            ids["run"],
            ids["actor"],
            ids["artifact"],
            ids["version"],
            "f" * 64,
        ),
    )
    cursor.execute(
        """
        INSERT INTO tool_executions (
            id, tenant_id, run_id, tool_name, capability, idempotency_key,
            request_fingerprint, input_sha256, target_resource_type,
            target_resource_id, approval_request_id, status
        ) VALUES (%s, %s, %s, 'publish_artifact', 'publish', %s, %s, %s,
                  'agent_artifact', %s, %s, 'pending')
        """,
        (
            ids["tool"],
            ids["tenant"],
            ids["run"],
            f"m4-tool-{ids['tool'].hex}",
            "1" * 64,
            "2" * 64,
            ids["artifact"],
            ids["approval"],
        ),
    )


@pytest.mark.integration
def test_m4_migration_enforces_contracts_and_downgrades_after_cleanup() -> None:
    _run_alembic("upgrade", "head")
    ids: dict[str, UUID] | None = None
    try:
        with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                "AND tablename = ANY(%s)",
                (list(AGENT_TABLES),),
            )
            assert {row[0] for row in cursor.fetchall()} == set(AGENT_TABLES)

            cursor.execute(
                """
                SELECT conname
                FROM pg_constraint
                WHERE conrelid IN (
                    'agent_runs'::regclass,
                    'agent_run_events'::regclass,
                    'approval_requests'::regclass,
                    'tool_executions'::regclass,
                    'agent_artifacts'::regclass
                )
                """
            )
            constraints = {row[0] for row in cursor.fetchall()}
            assert {
                "uq_agent_runs_tenant_id_idempotency_key",
                "uq_agent_run_events_run_id_seq",
                "ck_approval_requests_expiry_after_request",
                "ck_tool_executions_target_pair_valid",
                "ck_agent_artifacts_content_metadata_pair",
            } <= constraints

            ids = _seed_foundation(cursor)
            _insert_valid_agent_rows(cursor, ids)

            with pytest.raises(psycopg.errors.CheckViolation):
                with connection.transaction():
                    cursor.execute(
                        """
                        INSERT INTO agent_run_events (
                            id, tenant_id, run_id, seq, event_type, event_version, public_payload
                        ) VALUES (%s, %s, %s, 0, 'invalid', 1, '{}'::json)
                        """,
                        (uuid4(), ids["tenant"], ids["run"]),
                    )

            with pytest.raises(psycopg.errors.CheckViolation):
                with connection.transaction():
                    cursor.execute(
                        """
                        INSERT INTO agent_artifacts (
                            id, tenant_id, run_id, source_document_version_id, kind, status,
                            content_type, object_bucket, object_key, content_sha256,
                            behavior_versions
                        ) VALUES (%s, %s, %s, %s, 'broken', 'writing', 'application/json',
                                  'artifacts', %s, %s, '{}'::json)
                        """,
                        (
                            uuid4(),
                            ids["tenant"],
                            ids["run"],
                            ids["version"],
                            f"{ids['tenant']}/agent/broken.json",
                            "3" * 64,
                        ),
                    )

        failed_downgrade = _run_alembic("downgrade", "20260718_0008", check=False)
        assert failed_downgrade.returncode != 0
        assert "cannot downgrade while M4 Agent rows exist" in failed_downgrade.stderr

        with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("TRUNCATE {} CASCADE").format(
                    sql.SQL(", ").join(map(sql.Identifier, AGENT_TABLES))
                )
            )

        _run_alembic("downgrade", "20260718_0008")
        with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                "AND tablename = ANY(%s)",
                (list(AGENT_TABLES),),
            )
            assert cursor.fetchall() == []
            cursor.execute("SELECT to_regclass('public.document_chunks')")
            assert cursor.fetchone() == ("document_chunks",)
    finally:
        _run_alembic("upgrade", "head")
        if ids is not None:
            with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
                cursor.execute("DELETE FROM tenants WHERE id = %s", (ids["tenant"],))
                cursor.execute("DELETE FROM users WHERE id = %s", (ids["actor"],))
