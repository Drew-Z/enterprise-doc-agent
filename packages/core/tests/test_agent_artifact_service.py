from __future__ import annotations

import hashlib
import json
from uuid import uuid4

import pytest

from enterprise_doc_core.agents import (
    AgentArtifactIntegrityError,
    AgentArtifactService,
    AgentArtifactStoreUnavailable,
)
from enterprise_doc_core.agents.artifact_service import _parse_preview_payload
from enterprise_doc_core.telemetry import MetricsRuntime


async def test_agent_artifact_service_records_success_and_store_unavailable() -> None:
    metrics = MetricsRuntime.create()
    service = AgentArtifactService(
        session_factory=None,  # type: ignore[arg-type]
        artifact_store=object(),  # type: ignore[arg-type]
        metrics=metrics,
    )

    async def list_success(**_: object) -> tuple[()]:
        return ()

    service._list_for_run = list_success  # type: ignore[method-assign]
    assert (
        await service.list_for_run(
            tenant_id=uuid4(),
            actor_id=uuid4(),
            run_id=uuid4(),
        )
        == ()
    )

    async def download_unavailable(**_: object) -> object:
        raise AgentArtifactStoreUnavailable()

    service._get_download = download_unavailable  # type: ignore[method-assign]
    with pytest.raises(AgentArtifactStoreUnavailable):
        await service.get_download(
            tenant_id=uuid4(),
            actor_id=uuid4(),
            artifact_id=uuid4(),
        )

    async def preview_success(**_: object) -> object:
        return object()

    service._get_preview = preview_success  # type: ignore[method-assign]
    await service.get_preview(
        tenant_id=uuid4(),
        actor_id=uuid4(),
        artifact_id=uuid4(),
    )

    rendered = metrics.render().decode("utf-8")
    assert 'boundary="artifact",operation="list",result="success"' in rendered
    assert 'boundary="artifact",operation="download",result="retryable_error"' in rendered
    assert 'boundary="artifact",operation="read",result="success"' in rendered


def test_artifact_preview_parser_returns_verified_answer_and_citations() -> None:
    artifact_id = uuid4()
    run_id = uuid4()
    document_version_id = uuid4()
    chunk_id = uuid4()
    body = json.dumps(
        {
            "schema_version": 1,
            "run_id": str(run_id),
            "task_type": "question_answer",
            "answer_text": "Payment is due within 30 days.",
            "structured_fields": None,
            "risk_hint": "low",
            "citations": [
                {
                    "chunk_id": str(chunk_id),
                    "document_version_id": str(document_version_id),
                    "source_filename": "contract.pdf",
                    "page_number": 3,
                    "heading": "Payment terms",
                    "start_offset": 120,
                    "end_offset": 168,
                    "excerpt": "Invoices are payable within thirty calendar days.",
                }
            ],
            "behavior_versions": {
                "graph_version": "graph-v1",
                "prompt_version": "prompt-v1",
                "tool_schema_version": "tool-v1",
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")

    result = _parse_preview_payload(
        body,
        artifact_id=artifact_id,
        expected_run_id=run_id,
        expected_document_version_id=document_version_id,
        status="draft_ready",
        content_sha256=hashlib.sha256(body).hexdigest(),
    )

    assert result.answer_text == "Payment is due within 30 days."
    assert result.citations[0].chunk_id == chunk_id
    assert result.citations[0].page_number == 3
    assert result.behavior_versions.prompt_version == "prompt-v1"


def test_artifact_preview_parser_rejects_cross_version_citation() -> None:
    run_id = uuid4()
    body = json.dumps(
        {
            "schema_version": 1,
            "run_id": str(run_id),
            "task_type": "question_answer",
            "answer_text": "Answer",
            "structured_fields": None,
            "risk_hint": None,
            "citations": [
                {
                    "chunk_id": str(uuid4()),
                    "document_version_id": str(uuid4()),
                    "source_filename": None,
                    "page_number": None,
                    "heading": None,
                    "start_offset": 0,
                    "end_offset": 6,
                    "excerpt": "Answer",
                }
            ],
            "behavior_versions": {
                "graph_version": "graph-v1",
                "prompt_version": "prompt-v1",
                "tool_schema_version": "tool-v1",
            },
        }
    ).encode("utf-8")

    with pytest.raises(AgentArtifactIntegrityError):
        _parse_preview_payload(
            body,
            artifact_id=uuid4(),
            expected_run_id=run_id,
            expected_document_version_id=uuid4(),
            status="draft_ready",
            content_sha256=hashlib.sha256(body).hexdigest(),
        )
