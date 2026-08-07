from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import scripts.evaluate_staging_rag_quality as staging_quality

from enterprise_doc_core.evaluation import ReportProvenance, verify_report_payload
from enterprise_doc_core.evaluation.rag_quality import load_rag_quality_dataset


def _write_dataset(tmp_path: Path) -> Path:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    document = "# Vacation\nEmployees may carry over up to five unused vacation days.\n"
    (corpus / "policy.txt").write_text(document, encoding="utf-8")
    payload = {
        "schema_version": 1,
        "version": "runner-test-v1",
        "corpus_root": "corpus",
        "expected_category_counts": {"fact": 1, "refusal": 1},
        "documents": [
            {
                "document_key": "leave-policy",
                "path": "policy.txt",
                "media_type": "text/plain",
                "anchors": [
                    {
                        "anchor_id": "leave.carryover",
                        "section": "Vacation",
                        "page": None,
                        "quote": "Employees may carry over up to five unused vacation days.",
                    }
                ],
            }
        ],
        "cases": [
            {
                "case_id": "answer-case",
                "category": "fact",
                "document_key": "leave-policy",
                "query": "How many days may be carried over?",
                "expected_outcome": "answer",
                "facts": [
                    {
                        "fact_id": "carryover",
                        "accepted_answers": ["five unused vacation days"],
                        "forbidden_answers": ["ten vacation days"],
                        "anchor_ids": ["leave.carryover"],
                    }
                ],
                "expected_anchor_ids": ["leave.carryover"],
                "accepted_refusal_codes": [],
                "trial": True,
            },
            {
                "case_id": "refusal-case",
                "category": "refusal",
                "document_key": "leave-policy",
                "query": "What is the chief executive's full name?",
                "expected_outcome": "refusal",
                "facts": [],
                "expected_anchor_ids": [],
                "accepted_refusal_codes": ["insufficient_evidence"],
                "trial": True,
            },
        ],
        "targets": {
            "fact_recall": 1.0,
            "closed_label_fact_precision": 1.0,
            "grounded_fact_rate": 1.0,
            "citation_precision": 1.0,
            "citation_recall": 1.0,
            "refusal_precision": 1.0,
            "refusal_recall": 1.0,
            "refusal_reason_accuracy": 1.0,
        },
        "limitations": ["Synthetic runner fixture."],
    }
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class FakeClient:
    token = "token-secret-must-not-appear"
    base_url = "https://control-plane-secret.example"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.answer = "The policy allows five unused vacation days to be carried over."
        self.artifact_body = json.dumps(
            {
                "schema_version": 1,
                "run_id": "run-answer",
                "answer_text": self.answer,
                "citations": [
                    {
                        "chunk_id": "runtime-chunk-secret",
                        "document_version_id": "version-secret",
                        "source_filename": "policy.txt",
                        "page_number": None,
                        "heading": "Vacation",
                        "start_offset": 0,
                        "end_offset": 64,
                        "excerpt": ("Employees may carry over up to five unused vacation days."),
                    }
                ],
                "behavior_versions": {
                    "graph_version": "graph-v1",
                    "prompt_version": "prompt-v1",
                    "tool_schema_version": "tools-v1",
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    def request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expected_statuses: set[int] | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        del headers, expected_statuses
        self.calls.append((method, path))
        if path == "/api/upload-sessions":
            return {"sessionId": "upload-secret"}
        if path.endswith("/parts/1/presign"):
            return {
                "url": "https://object-secret.example/presigned-upload",
                "headers": {"x-checksum": "ok"},
            }
        if path.endswith("/complete"):
            return {"versionId": "version-secret"}
        if path == "/api/agent-runs/ready-document-versions":
            return [{"versionId": "version-secret"}]
        if path == "/api/agent-runs":
            assert payload is not None
            if "chief executive" in str(payload["inputText"]):
                return {"runId": "run-refusal"}
            return {"runId": "run-answer"}
        if path == "/api/agent-runs/run-answer":
            return {
                "status": "succeeded",
                "errorCode": None,
                "modelProvider": "openai_compatible",
                "modelName": "reviewed-chat-model",
                "modelVersion": "2026-08",
                "graphVersion": "graph-v1",
                "promptVersion": "prompt-v1",
                "toolSchemaVersion": "tools-v1",
            }
        if path == "/api/agent-runs/run-refusal":
            return {
                "status": "refused",
                "errorCode": None,
                "modelProvider": "openai_compatible",
                "modelName": "reviewed-chat-model",
                "modelVersion": "2026-08",
                "graphVersion": "graph-v1",
                "promptVersion": "prompt-v1",
                "toolSchemaVersion": "tools-v1",
            }
        if path == "/api/agent-runs/run-refusal/events":
            return [
                {
                    "eventType": "run.finished",
                    "publicPayload": {
                        "status": "refused",
                        "refusal_reason": "insufficient_evidence",
                    },
                }
            ]
        if path == "/api/agent-runs/run-answer/artifacts":
            return [
                {
                    "artifactId": "artifact-secret",
                    "kind": "answer",
                    "status": "draft_ready",
                    "contentSha256": hashlib.sha256(self.artifact_body).hexdigest(),
                    "sizeBytes": len(self.artifact_body),
                }
            ]
        if path == "/api/agent-artifacts/artifact-secret/download":
            return {
                "url": "https://object-secret.example/presigned-download",
                "contentSha256": hashlib.sha256(self.artifact_body).hexdigest(),
                "sizeBytes": len(self.artifact_body),
            }
        raise AssertionError((method, path, payload))

    def put_bytes(self, url: str, *, content: bytes, headers: dict[str, str]) -> str:
        assert url == "https://object-secret.example/presigned-upload"
        assert headers == {"x-checksum": "ok"}
        assert b"five unused vacation days" in content
        self.calls.append(("PUT", url))
        return '"etag-secret"'

    def get_bytes(self, url: str) -> bytes:
        assert url == "https://object-secret.example/presigned-download"
        self.calls.append(("GET", url))
        return self.artifact_body


def _provenance() -> ReportProvenance:
    return ReportProvenance(
        command=["python", "scripts/evaluate_staging_rag_quality.py", "<sanitized>"],
        environment={"execution_scope": "test"},
        commit_sha="a" * 40,
        working_tree_dirty=False,
        input_sha256="b" * 64,
    )


def test_staging_quality_runs_answer_and_refusal_without_leaking_runtime_data(
    tmp_path: Path,
) -> None:
    loaded = load_rag_quality_dataset(_write_dataset(tmp_path))
    client = FakeClient()

    report = staging_quality.run_staging_rag_quality(
        client,
        loaded=loaded,
        trial_only=True,
        timeout_seconds=30,
        run_nonce="fixed-test-run",
        monotonic=lambda: 1.0,
        sleep=lambda _: None,
        provenance=_provenance(),
    )

    assert report["status"] == "passed"
    assert report["coverage"] == "full"
    assert report["measured"]["fact_recall"] == 1.0
    assert report["measured"]["refusal_reason_accuracy"] == 1.0
    assert verify_report_payload(report)
    encoded = json.dumps(report, sort_keys=True)
    for forbidden in (
        client.token,
        client.base_url,
        "object-secret.example",
        "upload-secret",
        "version-secret",
        "run-answer",
        "run-refusal",
        "artifact-secret",
        "runtime-chunk-secret",
        client.answer,
        "Employees may carry over up to five unused vacation days.",
        str(tmp_path),
    ):
        assert forbidden not in encoded
    assert report["cases"][0]["answer_sha256"] == hashlib.sha256(client.answer.encode()).hexdigest()


def test_selection_errors_before_any_staging_request(tmp_path: Path) -> None:
    loaded = load_rag_quality_dataset(_write_dataset(tmp_path))
    client = FakeClient()

    with pytest.raises(ValueError, match="unknown case"):
        staging_quality.run_staging_rag_quality(
            client,
            loaded=loaded,
            case_ids=("missing",),
            timeout_seconds=30,
            provenance=_provenance(),
        )

    assert client.calls == []


def test_runner_source_accepts_token_only_from_environment() -> None:
    source = staging_quality.__file__
    assert source is not None
    text = Path(source).read_text(encoding="utf-8")

    assert 'add_argument("--token"' not in text
    assert 'os.environ.get("STAGING_SMOKE_TOKEN"' in text
    assert "print(token" not in text
