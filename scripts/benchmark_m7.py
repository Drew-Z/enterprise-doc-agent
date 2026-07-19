from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Never
from uuid import UUID

from enterprise_doc_core.agents import (
    BehaviorVersions,
    CircuitBreaker,
    DeterministicGroundedGateway,
    GroundedEvidence,
    GroundedModelRequest,
    ModelRouteDescriptor,
    ModelTimeoutError,
    RoutedChatModelGateway,
)
from enterprise_doc_core.agents.models import AgentRunTaskType
from enterprise_doc_core.evaluation import (
    ModelBenchmarkReport,
    ModelCostMetadata,
    ModelProviderHealthSnapshot,
    build_percentile_summary,
    capture_report_provenance,
    seal_report,
)
from enterprise_doc_core.evaluation.contracts import utc_now

ROOT = Path(__file__).resolve().parents[1]


class TimeoutGateway:
    async def generate(self, _: GroundedModelRequest) -> Never:
        raise ModelTimeoutError()


def _load_dataset(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    payload = json.loads(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise ValueError("benchmark dataset must contain cases")
    if not payload["cases"]:
        raise ValueError("benchmark dataset must contain at least one case")
    return payload, hashlib.sha256(raw).hexdigest()


def _request(case: dict[str, Any]) -> GroundedModelRequest:
    return GroundedModelRequest(
        task_type=AgentRunTaskType.QUESTION_ANSWER,
        user_input=str(case["input_text"]),
        evidence=[
            GroundedEvidence(
                chunk_id=UUID(str(case["chunk_id"])),
                tenant_id=UUID(str(case["tenant_id"])),
                document_version_id=UUID(str(case["document_version_id"])),
                generation_id=UUID(str(case["generation_id"])),
                text=str(case["evidence_text"]),
                rank=1,
                score=1.0,
                start_offset=0,
                end_offset=len(str(case["evidence_text"])),
            )
        ],
        behavior_versions=BehaviorVersions(
            graph_version="m4.v1",
            prompt_version="m4.v1",
            tool_schema_version="m4.v1",
        ),
    )


def _route_descriptor() -> ModelRouteDescriptor:
    return ModelRouteDescriptor(
        route_id="m7-local",
        provider="deterministic",
        model_name="deterministic-grounded",
        model_version="m7.benchmark.v1",
        quantization="none",
        context_window_tokens=8192,
        embedding_dimension=8,
    )


def _timeout_route_descriptor() -> ModelRouteDescriptor:
    return ModelRouteDescriptor(
        route_id="m7-local-timeout",
        provider="synthetic",
        model_name="timeout-gateway",
        model_version="m7.fault.v1",
    )


def _route(
    scenario: str,
) -> tuple[RoutedChatModelGateway | DeterministicGroundedGateway, ModelRouteDescriptor]:
    descriptor = _route_descriptor()
    if scenario == "fallback-contract":
        primary_descriptor = _timeout_route_descriptor()
        return (
            RoutedChatModelGateway(
                primary=TimeoutGateway(),
                primary_descriptor=primary_descriptor,
                fallback=DeterministicGroundedGateway(),
                fallback_descriptor=descriptor,
                breaker=CircuitBreaker(failure_threshold=2, cooldown_seconds=60),
            ),
            primary_descriptor,
        )
    return DeterministicGroundedGateway(), descriptor


def _route_metadata(
    gateway: RoutedChatModelGateway | DeterministicGroundedGateway,
    descriptor: ModelRouteDescriptor,
) -> dict[str, str | int | None]:
    metadata: dict[str, str | int | None] = {
        "route_id": descriptor.route_id,
        "provider": descriptor.provider,
        "model_name": descriptor.model_name,
        "model_version": descriptor.model_version,
        "quantization": descriptor.quantization,
        "context_window_tokens": descriptor.context_window_tokens,
        "embedding_dimension": descriptor.embedding_dimension,
    }
    if isinstance(gateway, RoutedChatModelGateway):
        fallback = gateway.fallback_descriptor
        metadata.update(
            {
                "primary_provider": gateway.primary_descriptor.provider,
                "primary_model_name": gateway.primary_descriptor.model_name,
                "primary_model_version": gateway.primary_descriptor.model_version,
                "fallback_provider": fallback.provider if fallback is not None else None,
                "fallback_model_name": fallback.model_name if fallback is not None else None,
                "fallback_model_version": fallback.model_version if fallback is not None else None,
            }
        )
    return metadata


def _report_command(args: argparse.Namespace) -> list[str]:
    command = [
        "python",
        "scripts/benchmark_m7.py",
        "--dataset",
        "<dataset>",
        "--scenario",
        str(args.scenario),
        "--iterations",
        str(args.iterations),
    ]
    if args.report_path is not None:
        command.extend(["--report-path", "<report-path>"])
    return command


async def _provider_health(
    gateway: RoutedChatModelGateway | DeterministicGroundedGateway,
    descriptor: ModelRouteDescriptor,
) -> dict[str, ModelProviderHealthSnapshot]:
    if isinstance(gateway, RoutedChatModelGateway):
        return {
            name: ModelProviderHealthSnapshot.model_validate(asdict(snapshot))
            for name, snapshot in (await gateway.healthcheck()).items()
        }
    available = await gateway.healthcheck()
    return {
        "primary": ModelProviderHealthSnapshot(
            available=available,
            provider=descriptor.provider,
            model_name=descriptor.model_name,
            model_version=descriptor.model_version,
            model_revision=descriptor.model_revision,
            quantization=descriptor.quantization,
            context_window_tokens=descriptor.context_window_tokens,
            embedding_dimension=descriptor.embedding_dimension,
            error_code=None if available else "provider_unavailable",
        )
    }


async def run_benchmark(
    *,
    dataset_path: Path,
    scenario: str,
    iterations: int,
    command: list[str] | None = None,
) -> ModelBenchmarkReport:
    payload, dataset_sha256 = _load_dataset(dataset_path)
    cases = payload["cases"]
    if iterations <= 0:
        raise ValueError("iterations must be positive")
    gateway, descriptor = _route(scenario)
    started_at = utc_now()
    latencies: list[float] = []
    errors: dict[str, int] = {}
    valid_citation_count = 0
    valid_cases = 0
    total_citations = 0
    successful_requests = 0
    for index in range(iterations):
        case = cases[index % len(cases)]
        started = time.perf_counter()
        try:
            output = await gateway.generate(_request(case))
            successful_requests += 1
            expected_chunk = str(case["chunk_id"])
            valid_for_case = sum(
                str(citation.chunk_id) == expected_chunk for citation in output.citations
            )
            valid_citation_count += valid_for_case
            total_citations += len(output.citations)
            if output.citations and valid_for_case == len(output.citations):
                valid_cases += 1
        except Exception as error:
            code = str(getattr(error, "code", type(error).__name__))
            errors[code] = errors.get(code, 0) + 1
        latencies.append((time.perf_counter() - started) * 1000)
    fallback_count = gateway.fallback_count if isinstance(gateway, RoutedChatModelGateway) else 0
    breaker_state = (
        gateway.breaker.state.value
        if isinstance(gateway, RoutedChatModelGateway)
        else "not_applicable"
    )
    citation_precision = valid_citation_count / total_citations if total_citations else 0.0
    valid_case_rate = valid_cases / successful_requests if successful_requests else 0.0
    fallback_contract_passed = scenario != "fallback-contract" or (
        not errors and fallback_count == iterations and breaker_state == "open"
    )
    provider_health = await _provider_health(gateway, descriptor)
    provenance = capture_report_provenance(
        command=command
        or [
            "internal",
            "scripts.benchmark_m7.run_benchmark",
            "--scenario",
            scenario,
            "--iterations",
            str(iterations),
        ],
        root=ROOT,
        execution_scope="local-model-benchmark",
        input_sha256=dataset_sha256,
    )
    return seal_report(
        ModelBenchmarkReport(
            scenario=scenario,
            status=(
                "passed"
                if not errors and valid_case_rate == 1.0 and fallback_contract_passed
                else "failed"
            ),
            route=_route_metadata(gateway, descriptor),
            dataset_version=str(payload["version"]),
            dataset_sha256=dataset_sha256,
            sample_count=iterations,
            latency_ms=build_percentile_summary(latencies),
            errors_by_code=errors,
            fallback_count=fallback_count,
            breaker_state=breaker_state,
            provider_health=provider_health,
            cost_metadata=ModelCostMetadata(
                source="not_available",
                limitation=(
                    "The deterministic local gateway does not report token usage or provider "
                    "pricing; no monetary cost is claimed."
                ),
            ),
            citation_validity={
                "valid_cases": valid_cases,
                "total_cases": iterations,
                "valid_citation_count": valid_citation_count,
                "citation_precision": citation_precision,
                "valid_case_rate": valid_case_rate,
                "citation_count": total_citations,
            },
            targets=(
                {
                    "citation_precision": 1.0,
                    "valid_case_rate": 1.0,
                    "fallback_count": iterations,
                    "breaker_state": "open",
                }
                if scenario == "fallback-contract"
                else {"citation_precision": 1.0, "valid_case_rate": 1.0}
            ),
            measured={
                "citation_precision": citation_precision,
                "valid_case_rate": valid_case_rate,
                "fallback_count": fallback_count,
                "successful_requests": successful_requests,
            },
            limitations=[
                *list(payload.get("limitations", [])),
                "This benchmark is a local orchestration baseline, not a GPU/vLLM throughput "
                "or memory result.",
                "Real provider quality, cost, network latency and queue contention require a "
                "separate manual gate.",
            ],
            provenance=provenance,
            started_at=started_at,
            completed_at=utc_now(),
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the M7 model route benchmark")
    parser.add_argument(
        "--dataset", type=Path, default=Path("evaluation/m7_model_benchmark_v1.json")
    )
    parser.add_argument(
        "--scenario",
        choices=("deterministic", "fallback-contract"),
        default="deterministic",
    )
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--report-path", type=Path)
    args = parser.parse_args()
    if args.iterations <= 0:
        raise SystemExit("iterations must be positive")
    report = asyncio.run(
        run_benchmark(
            dataset_path=args.dataset,
            scenario=args.scenario,
            iterations=args.iterations,
            command=_report_command(args),
        )
    )
    rendered = report.model_dump_json(indent=2)
    if args.report_path is not None:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if report.status != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
