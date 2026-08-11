from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from enterprise_doc_core.evaluation import (
    ReportProvenance,
    build_percentile_summary,
    capture_report_provenance,
    seal_report_payload,
)
from enterprise_doc_core.evaluation.rag_quality import (
    LoadedRagQualityDataset,
    ObservedCitation,
    RagQualityCase,
    RagQualityObservation,
    aggregate_rag_quality_scores,
    load_rag_quality_dataset,
    score_rag_quality_case,
)
from enterprise_doc_core.jobs import is_allowed_job_diagnostic_code

if TYPE_CHECKING:
    from scripts.staging_smoke import SmokeClient, UrlLibSmokeClient
else:
    try:
        from scripts.staging_smoke import SmokeClient, UrlLibSmokeClient
    except ModuleNotFoundError:
        from staging_smoke import SmokeClient, UrlLibSmokeClient


ROOT = Path(__file__).resolve().parents[1]
EVALUATOR_VERSION = "m5.rag-quality.v3"
_TERMINAL_STATUSES = frozenset(
    {"cancelled", "expired", "failed", "refused", "rejected", "succeeded"}
)


class StagingRagQualityFailure(RuntimeError):
    pass


def _required_mapping(value: object, description: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise StagingRagQualityFailure(f"{description} was not a JSON object")
    return value


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise StagingRagQualityFailure(f"response omitted required field {key}")
    return value


def _optional_str(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def _required_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise StagingRagQualityFailure(f"response omitted required field {key}")
    return value


def _opaque_id(*parts: str) -> str:
    encoded = "\0".join(parts).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:32]


def select_rag_quality_cases(
    loaded: LoadedRagQualityDataset,
    *,
    case_ids: tuple[str, ...] = (),
    trial_only: bool = False,
    max_cases: int | None = None,
) -> tuple[RagQualityCase, ...]:
    if max_cases is not None and max_cases <= 0:
        raise ValueError("max_cases must be positive")
    by_id = loaded.dataset.cases_by_id
    if case_ids:
        unknown = [case_id for case_id in case_ids if case_id not in by_id]
        if unknown:
            raise ValueError(f"unknown case id: {unknown[0]}")
        selected = tuple(by_id[case_id] for case_id in dict.fromkeys(case_ids))
    else:
        selected = tuple(case for case in loaded.dataset.cases if not trial_only or case.trial)
    if max_cases is not None:
        selected = selected[:max_cases]
    if not selected:
        raise ValueError("case selection is empty")
    return selected


def _upload_document(
    client: SmokeClient,
    *,
    loaded: LoadedRagQualityDataset,
    document_key: str,
    run_nonce: str,
) -> str:
    document = loaded.dataset.documents_by_key[document_key]
    content = loaded.documents[document_key]
    digest = hashlib.sha256(content).hexdigest()
    checksum = base64.b64encode(hashlib.sha256(content).digest()).decode("ascii")
    created = _required_mapping(
        client.request_json(
            "POST",
            "/api/upload-sessions",
            payload={
                "filename": Path(document.path).name,
                "sizeBytes": len(content),
                "mediaType": document.media_type,
                "sha256": digest,
            },
            headers={
                "Idempotency-Key": _opaque_id(
                    "rag-quality-upload",
                    run_nonce,
                    loaded.dataset.version,
                    document_key,
                )
            },
            expected_statuses={200, 201},
        ),
        "upload creation response",
    )
    session_id = _required_str(created, "sessionId")
    session_path = f"/api/upload-sessions/{session_id}"
    presign = _required_mapping(
        client.request_json(
            "POST",
            f"{session_path}/parts/1/presign",
            payload={"sizeBytes": len(content), "checksumSha256": checksum},
        ),
        "part presign response",
    )
    raw_headers = presign.get("headers")
    if not isinstance(raw_headers, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in raw_headers.items()
    ):
        raise StagingRagQualityFailure("part presign response returned invalid headers")
    etag = client.put_bytes(
        _required_str(presign, "url"),
        content=content,
        headers=raw_headers,
    )
    completed = _required_mapping(
        client.request_json(
            "POST",
            f"{session_path}/complete",
            payload={
                "parts": [
                    {
                        "partNumber": 1,
                        "sizeBytes": len(content),
                        "etag": etag,
                        "checksumSha256": checksum,
                    }
                ]
            },
        ),
        "upload completion response",
    )
    return _required_str(completed, "versionId")


def _wait_for_ready_version(
    client: SmokeClient,
    *,
    version_id: str,
    deadline: float,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> None:
    while monotonic() < deadline:
        payload = client.request_json("GET", "/api/agent-runs/ready-document-versions")
        if not isinstance(payload, list):
            raise StagingRagQualityFailure("ready-document response was not a JSON list")
        if any(isinstance(item, dict) and item.get("versionId") == version_id for item in payload):
            return
        sleep(2.0)
    raise StagingRagQualityFailure("document ingestion did not reach ready before timeout")


def _wait_for_run(
    client: SmokeClient,
    *,
    run_id: str,
    deadline: float,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    while monotonic() < deadline:
        status = _required_mapping(
            client.request_json("GET", f"/api/agent-runs/{run_id}"),
            "Agent status response",
        )
        if _required_str(status, "status") in _TERMINAL_STATUSES:
            return status
        sleep(2.0)
    raise StagingRagQualityFailure("Agent run did not reach terminal status before timeout")


def _refusal_reason(client: SmokeClient, *, run_id: str) -> str | None:
    events = client.request_json("GET", f"/api/agent-runs/{run_id}/events")
    if not isinstance(events, list):
        raise StagingRagQualityFailure("Agent events response was not a JSON list")
    for event in reversed(events):
        if not isinstance(event, dict) or event.get("eventType") != "run.finished":
            continue
        payload = event.get("publicPayload")
        if not isinstance(payload, dict):
            continue
        reason = payload.get("refusal_reason", payload.get("refusalReason"))
        if isinstance(reason, str) and reason:
            return reason
    return None


def _load_answer_artifact(
    client: SmokeClient,
    *,
    run_id: str,
    version_id: str,
    document_key: str,
) -> tuple[str, tuple[ObservedCitation, ...]]:
    artifacts = client.request_json("GET", f"/api/agent-runs/{run_id}/artifacts")
    if not isinstance(artifacts, list):
        raise StagingRagQualityFailure("Agent artifact response was not a JSON list")
    answers = [
        item
        for item in artifacts
        if isinstance(item, dict)
        and item.get("kind") == "answer"
        and item.get("status") == "draft_ready"
    ]
    if len(answers) != 1:
        raise StagingRagQualityFailure("Agent run did not expose one ready answer artifact")
    answer = answers[0]
    artifact_id = _required_str(answer, "artifactId")
    expected_hash = _required_str(answer, "contentSha256")
    expected_size = _required_int(answer, "sizeBytes")
    download = _required_mapping(
        client.request_json("GET", f"/api/agent-artifacts/{artifact_id}/download"),
        "artifact download response",
    )
    if _required_str(download, "contentSha256") != expected_hash:
        raise StagingRagQualityFailure("artifact download metadata changed SHA-256")
    if _required_int(download, "sizeBytes") != expected_size:
        raise StagingRagQualityFailure("artifact download metadata changed size")
    body = client.get_bytes(_required_str(download, "url"))
    if len(body) != expected_size or hashlib.sha256(body).hexdigest() != expected_hash:
        raise StagingRagQualityFailure("downloaded artifact failed integrity verification")
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise StagingRagQualityFailure("downloaded artifact was not valid JSON") from error
    artifact = _required_mapping(payload, "downloaded artifact")
    if _required_str(artifact, "run_id") != run_id:
        raise StagingRagQualityFailure("downloaded artifact referenced another run")
    answer_text = _required_str(artifact, "answer_text")
    raw_citations = artifact.get("citations")
    if not isinstance(raw_citations, list):
        raise StagingRagQualityFailure("downloaded artifact citations were not a list")
    citations: list[ObservedCitation] = []
    for raw in raw_citations:
        citation = _required_mapping(raw, "artifact citation")
        cited_version = _required_str(citation, "document_version_id")
        page = citation.get("page_number")
        heading = citation.get("heading")
        citations.append(
            ObservedCitation(
                runtime_chunk_id=_required_str(citation, "chunk_id"),
                document_key=(document_key if cited_version == version_id else "unmapped"),
                page=page if isinstance(page, int) and not isinstance(page, bool) else None,
                heading=heading if isinstance(heading, str) and heading else None,
                excerpt=_required_str(citation, "excerpt"),
            )
        )
    return answer_text, tuple(citations)


def _safe_runtime_identity(status: dict[str, Any]) -> tuple[dict[str, str | None], dict[str, str]]:
    route = {
        "provider": _required_str(status, "modelProvider"),
        "model_name": _required_str(status, "modelName"),
        "model_version": _optional_str(status, "modelVersion"),
    }
    behavior = {
        "graph": _required_str(status, "graphVersion"),
        "prompt": _required_str(status, "promptVersion"),
        "tool_schema": _required_str(status, "toolSchemaVersion"),
    }
    return route, behavior


def _thresholds_passed(measured: dict[str, float | None], targets: dict[str, float]) -> bool:
    for name, threshold in targets.items():
        value = measured.get(name)
        if value is None or value < threshold:
            return False
    return True


def _safe_attempt_diagnostic(status: dict[str, Any]) -> str | None:
    executions = status.get("executions")
    if not isinstance(executions, list):
        return None
    for raw_execution in reversed(executions):
        if not isinstance(raw_execution, dict):
            continue
        attempts = raw_execution.get("attemptHistory")
        if not isinstance(attempts, list):
            continue
        for raw_attempt in reversed(attempts):
            if not isinstance(raw_attempt, dict):
                continue
            diagnostic = raw_attempt.get("diagnosticCode")
            if is_allowed_job_diagnostic_code(diagnostic):
                assert isinstance(diagnostic, str)
                return diagnostic
    return None


def run_staging_rag_quality(
    client: SmokeClient,
    *,
    loaded: LoadedRagQualityDataset,
    case_ids: tuple[str, ...] = (),
    trial_only: bool = False,
    max_cases: int | None = None,
    timeout_seconds: float,
    run_nonce: str | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    provenance: ReportProvenance | None = None,
) -> dict[str, Any]:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    selected = select_rag_quality_cases(
        loaded,
        case_ids=case_ids,
        trial_only=trial_only,
        max_cases=max_cases,
    )
    started_at = datetime.now(UTC)
    started = monotonic()
    deadline = started + timeout_seconds
    nonce = run_nonce or uuid4().hex
    document_keys = tuple(dict.fromkeys(case.document_key for case in selected))
    version_by_document: dict[str, str] = {}
    for document_key in document_keys:
        version_id = _upload_document(
            client,
            loaded=loaded,
            document_key=document_key,
            run_nonce=nonce,
        )
        _wait_for_ready_version(
            client,
            version_id=version_id,
            deadline=deadline,
            monotonic=monotonic,
            sleep=sleep,
        )
        version_by_document[document_key] = version_id

    scores = []
    case_results: list[dict[str, Any]] = []
    routes: dict[str, dict[str, str | None]] = {}
    behaviors: dict[str, dict[str, str]] = {}
    errors: Counter[str] = Counter()
    for case in selected:
        case_started = monotonic()
        version_id = version_by_document[case.document_key]
        created = _required_mapping(
            client.request_json(
                "POST",
                "/api/agent-runs",
                payload={
                    "documentVersionId": version_id,
                    "taskType": "question_answer",
                    "inputText": case.query,
                    "publishRequested": False,
                },
                headers={
                    "Idempotency-Key": _opaque_id(
                        "rag-quality-agent",
                        nonce,
                        loaded.dataset.version,
                        case.case_id,
                    )
                },
                expected_statuses={200, 202},
            ),
            "Agent creation response",
        )
        run_id = _required_str(created, "runId")
        status = _wait_for_run(
            client,
            run_id=run_id,
            deadline=deadline,
            monotonic=monotonic,
            sleep=sleep,
        )
        route, behavior = _safe_runtime_identity(status)
        route_key = json.dumps(route, sort_keys=True)
        behavior_key = json.dumps(behavior, sort_keys=True)
        routes[route_key] = route
        behaviors[behavior_key] = behavior
        terminal_status = _required_str(status, "status")
        outcome_code = _optional_str(status, "errorCode")
        failure_diagnostic_code = _safe_attempt_diagnostic(status)
        answer_text: str | None = None
        citations: tuple[ObservedCitation, ...] = ()
        if terminal_status == "succeeded":
            answer_text, citations = _load_answer_artifact(
                client,
                run_id=run_id,
                version_id=version_id,
                document_key=case.document_key,
            )
        elif terminal_status in {"refused", "rejected"}:
            outcome_code = outcome_code or _refusal_reason(client, run_id=run_id)
        if outcome_code is not None:
            errors[outcome_code] += 1
        duration_ms = max(0.0, (monotonic() - case_started) * 1000)
        observation = RagQualityObservation(
            terminal_status=terminal_status,
            answer_text=answer_text,
            citations=citations,
            error_code=outcome_code,
            duration_ms=duration_ms,
        )
        score = score_rag_quality_case(loaded.dataset, case, observation)
        scores.append(score)
        case_results.append(
            {
                "case_id": case.case_id,
                "category": case.category.value,
                "passed": score.passed,
                "terminal_status": score.terminal_status,
                "outcome_code": score.error_code,
                "duration_ms": score.duration_ms,
                "query_sha256": hashlib.sha256(case.query.encode("utf-8")).hexdigest(),
                "answer_sha256": (
                    hashlib.sha256(answer_text.encode("utf-8")).hexdigest()
                    if answer_text is not None
                    else None
                ),
                "answer_char_count": len(answer_text) if answer_text is not None else None,
                "matched_fact_ids": list(score.matched_fact_ids),
                "forbidden_fact_ids": list(score.forbidden_fact_ids),
                "matched_anchor_ids": list(score.matched_anchor_ids),
                "citation_diagnostics": [
                    {
                        "ordinal": diagnostic.ordinal,
                        "resolved": diagnostic.resolved,
                        "resolved_anchor_ids": list(diagnostic.resolved_anchor_ids),
                    }
                    for diagnostic in score.citation_diagnostics
                ],
                "unresolved_citation_count": score.unresolved_citation_count,
                "unexpected_anchor_ids": list(score.unexpected_anchor_ids),
                "failure_diagnostic_code": failure_diagnostic_code,
                "metrics": {
                    "fact_recall": score.fact_recall,
                    "closed_label_fact_precision": score.closed_label_fact_precision,
                    "grounded_fact_rate": score.grounded_fact_rate,
                    "citation_precision": score.citation_precision,
                    "citation_recall": score.citation_recall,
                    "refusal_reason_correct": score.refusal_reason_correct,
                },
            }
        )

    aggregate = aggregate_rag_quality_scores(tuple(scores))
    measured = {
        "fact_recall": aggregate.fact_recall,
        "closed_label_fact_precision": aggregate.closed_label_fact_precision,
        "grounded_fact_rate": aggregate.grounded_fact_rate,
        "citation_precision": aggregate.citation_precision,
        "citation_recall": aggregate.citation_recall,
        "refusal_precision": aggregate.refusal_precision,
        "refusal_recall": aggregate.refusal_recall,
        "refusal_reason_accuracy": aggregate.refusal_reason_accuracy,
    }
    input_hash = hashlib.sha256(
        f"{loaded.dataset_sha256}:{loaded.corpus_sha256}".encode("ascii")
    ).hexdigest()
    resolved_provenance = provenance or capture_report_provenance(
        command=[
            "python",
            "scripts/evaluate_staging_rag_quality.py",
            "--dataset",
            "<dataset>",
            "--base-url",
            "<staging-base-url>",
            "--allowed-host",
            "<control-plane-host>",
            "--allowed-object-store-host",
            "<object-store-host>",
        ],
        root=ROOT,
        execution_scope="authenticated-staging-real-provider-quality",
        input_sha256=input_hash,
    )
    coverage = "full" if len(selected) == len(loaded.dataset.cases) else "bounded_sample"
    report: dict[str, object] = {
        "schema_version": 1,
        "evaluator_version": EVALUATOR_VERSION,
        "suite": "staging-real-provider-rag-quality",
        "status": "passed" if _thresholds_passed(measured, loaded.dataset.targets) else "failed",
        "coverage": coverage,
        "dataset_version": loaded.dataset.version,
        "dataset_sha256": loaded.dataset_sha256,
        "corpus_sha256": loaded.corpus_sha256,
        "selected_case_count": len(selected),
        "total_case_count": len(loaded.dataset.cases),
        "trial_only": trial_only,
        "targets": loaded.dataset.targets,
        "measured": measured,
        "latency_ms": build_percentile_summary(list(aggregate.duration_ms)),
        "errors_by_code": dict(sorted(errors.items())),
        "provider_routes": list(routes.values()),
        "behavior_versions": list(behaviors.values()),
        "cost_metadata": {
            "source": "not_available",
            "limitation": (
                "The current staging status and artifact APIs do not expose provider token "
                "usage or billing data."
            ),
        },
        "cases": case_results,
        "limitations": [
            *loaded.dataset.limitations,
            (
                "This is a bounded sample and does not represent the complete 40-case suite."
                if coverage == "bounded_sample"
                else "This run covers the complete synthetic 40-case suite."
            ),
            "One execution does not establish repeatability across provider runs.",
            "Human semantic review remains outstanding.",
        ],
        "provenance": resolved_provenance.model_dump(mode="json"),
        "started_at": started_at.isoformat(),
        "completed_at": datetime.now(UTC).isoformat(),
    }
    return seal_report_payload(report)


def build_validation_report(
    *,
    loaded: LoadedRagQualityDataset,
    selected: tuple[RagQualityCase, ...],
    provenance: ReportProvenance,
) -> dict[str, Any]:
    report: dict[str, object] = {
        "schema_version": 1,
        "evaluator_version": EVALUATOR_VERSION,
        "suite": "staging-real-provider-rag-quality-validation",
        "status": "passed",
        "dataset_version": loaded.dataset.version,
        "dataset_sha256": loaded.dataset_sha256,
        "corpus_sha256": loaded.corpus_sha256,
        "selected_case_count": len(selected),
        "total_case_count": len(loaded.dataset.cases),
        "selected_case_ids": [case.case_id for case in selected],
        "provenance": provenance.model_dump(mode="json"),
        "limitations": ["No staging, embedding, retrieval, or model call was executed."],
    }
    return seal_report_payload(report)


def _report_command(args: argparse.Namespace) -> list[str]:
    command = [
        "python",
        "scripts/evaluate_staging_rag_quality.py",
        "--dataset",
        "<dataset>",
    ]
    if args.validate_only:
        command.append("--validate-only")
    else:
        command.extend(
            [
                "--base-url",
                "<staging-base-url>",
                "--allowed-host",
                "<control-plane-host>",
                "--allowed-object-store-host",
                "<object-store-host>",
            ]
        )
    if args.trial_only:
        command.append("--trial-only")
    if args.max_cases is not None:
        command.extend(["--max-cases", str(args.max_cases)])
    for _ in args.case_id:
        command.extend(["--case-id", "<case-id>"])
    if args.report_path is not None:
        command.extend(["--report-path", "<report-path>"])
    return command


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate real staging RAG and Agent quality")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("evaluation/rag_quality_v1.json"),
    )
    parser.add_argument("--base-url")
    parser.add_argument("--allowed-host", action="append", default=[])
    parser.add_argument("--allowed-object-store-host", action="append", default=[])
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--trial-only", action="store_true")
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    parser.add_argument("--report-path", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--allow-loopback-http", action="store_true")
    args = parser.parse_args()

    loaded = load_rag_quality_dataset(args.dataset)
    selected = select_rag_quality_cases(
        loaded,
        case_ids=tuple(args.case_id),
        trial_only=args.trial_only,
        max_cases=args.max_cases,
    )
    input_hash = hashlib.sha256(
        f"{loaded.dataset_sha256}:{loaded.corpus_sha256}".encode("ascii")
    ).hexdigest()
    provenance = capture_report_provenance(
        command=_report_command(args),
        root=ROOT,
        execution_scope=(
            "local-dataset-validation"
            if args.validate_only
            else "authenticated-staging-real-provider-quality"
        ),
        input_sha256=input_hash,
    )
    if args.validate_only:
        report = build_validation_report(
            loaded=loaded,
            selected=selected,
            provenance=provenance,
        )
    else:
        token = os.environ.get("STAGING_SMOKE_TOKEN", "")
        if not token:
            raise SystemExit("STAGING_SMOKE_TOKEN is required")
        if not args.base_url or not args.allowed_host or not args.allowed_object_store_host:
            raise SystemExit("base URL and both host allowlists are required")
        report = run_staging_rag_quality(
            UrlLibSmokeClient(
                base_url=args.base_url,
                token=token,
                allowed_control_plane_hosts=tuple(args.allowed_host),
                allowed_object_store_hosts=tuple(args.allowed_object_store_host),
                allow_loopback_http=args.allow_loopback_http,
            ),
            loaded=loaded,
            case_ids=tuple(args.case_id),
            trial_only=args.trial_only,
            max_cases=args.max_cases,
            timeout_seconds=args.timeout_seconds,
            provenance=provenance,
        )
    rendered = json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if args.report_path is not None:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
