"""Single-pass fixture generation evaluation; no upload, retrieval or tenant writes.

The collector never reads gold answers. Use the same configured presales route,
but keep these results separate from public end-to-end quality measurements.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

import httpx
from dotenv import dotenv_values
from pydantic import SecretStr

from enterprise_doc_core.config import ModelProvider, ModelSettings
from enterprise_doc_core.db import selector_event_loop_factory
from enterprise_doc_core.model_response import (
    MAX_MODEL_STREAM_BYTES,
    ModelResponseError,
    OpenAIResponseReader,
)
from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.gateway import OpenAICompatiblePresalesGateway
from enterprise_doc_core.presales.schemas import GenerationInput, SourceSnapshot
from enterprise_doc_core.presales.settings import PresalesSettings
from scripts.evaluate_presales_quality import Dataset, PublicDataset, load_dataset, write_json

ModelRoute = Literal["primary", "fallback"]


def load_route_settings(provider_env: Path, model_route: ModelRoute) -> ModelSettings:
    """Load only the explicitly selected route; never exchange stored credentials."""
    if model_route not in {"primary", "fallback"}:
        raise ValueError("invalid_model_route")
    values = dotenv_values(provider_env)
    prefix = "FALLBACK_" if model_route == "fallback" else ""
    selected = {
        "provider": ModelProvider.OPENAI_COMPATIBLE,
        "base_url": values[prefix + "BASE_URL"],
        "api_key": SecretStr(values[prefix + "API_KEY"] or ""),
        "model_name": values[prefix + "MODEL_NAME"],
        "reasoning_effort": values.get(prefix + "REASONING_EFFORT") or None,
        "streaming": values.get(prefix + "STREAMING") or False,
        "timeout_seconds": 120,
    }
    if model_route == "fallback":
        selected = {"fallback_" + key: value for key, value in selected.items()}
    return ModelSettings.model_validate(selected)


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(
        self, inner: httpx.AsyncBaseTransport, *, max_output_bytes: int | None = None
    ) -> None:
        self.inner = inner
        self.max_output_bytes = (
            ModelSettings().max_output_bytes if max_output_bytes is None else max_output_bytes
        )
        if self.max_output_bytes <= 0:
            raise ValueError("positive response limit required")
        self.records: list[dict[str, Any]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        record: dict[str, Any] = {"requestDispatched": True}
        self.records.append(record)
        # Only frozen fixture input and bounded output, never headers or credentials.
        request_body = json.loads(request.content)
        record["input"] = json.loads(request_body["messages"][1]["content"])
        if request_body.get("response_format", {}).get("type") == "json_schema":
            record["responseFormat"] = request_body["response_format"]
        phase = "awaiting_response_headers"
        try:
            response = await self.inner.handle_async_request(request)
            record["httpStatus"] = response.status_code
            streaming = response.status_code == 200 and request_body.get("stream") is True
            wire_limit = MAX_MODEL_STREAM_BYTES if streaming else self.max_output_bytes
            phase = "reading_response_body"
            body = bytearray()
            try:
                async for part in response.aiter_bytes():
                    record["decodedWireBytes"] = len(body) + len(part)
                    if len(body) + len(part) > wire_limit:
                        record["outputTooLarge"] = True
                        raise PresalesError("presales_output_too_large", provider_requests=1)
                    body.extend(part)
            finally:
                await response.aclose()
        except httpx.HTTPError as error:
            # Use fixed library names, never exception text, URLs or custom class names.
            error_type = next(
                (
                    kind.__name__
                    for kind in (
                        httpx.ConnectTimeout,
                        httpx.ReadTimeout,
                        httpx.WriteTimeout,
                        httpx.PoolTimeout,
                        httpx.ConnectError,
                        httpx.ReadError,
                        httpx.WriteError,
                        httpx.CloseError,
                        httpx.ProxyError,
                        httpx.RemoteProtocolError,
                        httpx.LocalProtocolError,
                        httpx.UnsupportedProtocol,
                    )
                    if isinstance(error, kind)
                ),
                "HTTPError",
            )
            record["transportFailure"] = {"type": error_type, "phase": phase}
            raise
        except asyncio.CancelledError:
            record["transportFailure"] = {"type": "CancelledError", "phase": phase}
            raise
        if response.status_code == 200:
            try:
                if streaming:
                    reader = OpenAIResponseReader(
                        streaming=True,
                        max_bytes=self.max_output_bytes,
                        max_stream_bytes=MAX_MODEL_STREAM_BYTES,
                    )
                    decoded = await reader.read(
                        httpx.Response(
                            200,
                            content=bytes(body),
                            headers={"content-type": response.headers.get("content-type", "")},
                            request=request,
                        )
                    )
                    raw = decoded.json()
                else:
                    raw = json.loads(body)
                record["response"] = {
                    k: raw[k] for k in ("id", "model", "choices", "usage") if k in raw
                }
            except (ValueError, TypeError, ModelResponseError):
                record["invalidJson"] = True
        return httpx.Response(
            response.status_code,
            content=bytes(body),
            request=request,
            headers={"content-type": response.headers.get("content-type", "")},
        )

    async def aclose(self) -> None:
        await self.inner.aclose()


def synthetic_sources(
    dataset: Dataset | PublicDataset,
    digest: str,
) -> tuple[list[SourceSnapshot], list[dict[str, str]]]:
    if any(len(source.content) > 1800 for source in dataset.sources):
        raise ValueError("direct_evidence_source_too_long")
    snapshots = [
        SourceSnapshot(
            version_id=uuid5(NAMESPACE_URL, digest + source.key + ":version"),
            document_id=uuid5(NAMESPACE_URL, digest + source.key + ":document"),
            generation_id=uuid5(NAMESPACE_URL, digest + source.key + ":generation"),
            applicability=source.applicability,
            filename=source.filename,
            version_number=1,
            latest_version_number=1,
            content_sha256=hashlib.sha256(source.content.encode()).hexdigest(),
        )
        for source in dataset.sources
    ]
    evidence = [
        {
            "chunkId": str(uuid5(NAMESPACE_URL, digest + source.key + ":chunk")),
            "documentVersionId": str(snapshot.version_id),
            "text": source.content,
            "filename": source.filename,
            "heading": "",
            "pageNumber": "",
        }
        for source, snapshot in zip(dataset.sources, snapshots, strict=True)
    ]
    return snapshots, evidence


async def collect(
    dataset_path: Path,
    output: Path,
    settings: ModelSettings,
    *,
    model_route: ModelRoute = "fallback",
    model_timeout_seconds: float = 120,
    transport: httpx.AsyncBaseTransport | None = None,
    strict_output: bool = False,
) -> dict[str, Any]:
    presales_settings = PresalesSettings(
        model_route=model_route,
        model_timeout_seconds=model_timeout_seconds,
        row_timeout_seconds=model_timeout_seconds + 30,
        primary_strict_output=strict_output if model_route == "primary" else False,
        fallback_strict_output=strict_output if model_route == "fallback" else False,
    )
    dataset, digest = load_dataset(dataset_path)
    snapshots, evidence = synthetic_sources(dataset, digest)
    report: dict[str, Any] = {
        "schemaVersion": "presales-gateway-run-v9" if strict_output else "presales-gateway-run-v6",
        "scope": (
            "generation_only_with_complete_synthetic_sources; no_retrieval_or_persistence"
            if dataset.synthetic
            else "generation_only_with_public_excerpts; no_retrieval_or_persistence"
        ),
        "datasetSha256": digest,
        "runnerSha256": hashlib.sha256(
            await asyncio.to_thread(Path(__file__).read_bytes)
        ).hexdigest(),
        "startedAt": datetime.now(UTC).isoformat(),
        "maxGenerationAttempts": len(dataset.requirements),
        "automaticRetries": 0,
        "selectedRoute": model_route,
        "configuredModelTimeoutSeconds": model_timeout_seconds,
        "configuredModelName": settings.fallback_model_name
        if model_route == "fallback"
        else settings.model_name,
        "configuredReasoningEffort": settings.fallback_reasoning_effort
        if model_route == "fallback"
        else settings.reasoning_effort,
        "configuredStreaming": settings.fallback_streaming
        if model_route == "fallback"
        else settings.streaming,
        "versions": {
            s.key: str(v.version_id) for s, v in zip(dataset.sources, snapshots, strict=True)
        },
        "observations": [],
        "status": "running",
    }
    write_json(output, report, exclusive=True)
    try:
        for requirement in dataset.requirements:
            recorder = RecordingTransport(
                transport or httpx.AsyncHTTPTransport(retries=0),
                max_output_bytes=settings.max_output_bytes,
            )
            gateway = OpenAICompatiblePresalesGateway(
                settings,
                presales_settings=presales_settings,
                transport=recorder,
            )
            source_input = GenerationInput(
                requirement=requirement, sources=snapshots, evidence=evidence
            )
            observation: dict[str, Any] = {
                "key": requirement.key,
                "startedAt": datetime.now(UTC).isoformat(),
                "state": "dispatching",
                "provenance": gateway.provenance,
                "sourceInput": source_input.model_dump(mode="json", by_alias=True),
            }
            report["observations"].append(observation)
            write_json(output, report)
            started = time.monotonic()
            try:
                result = await gateway.generate(source_input)
                observation.update(
                    state="succeeded", result=result.model_dump(mode="json", by_alias=True)
                )
            except PresalesError as error:
                observation.update(state="failed", errorCode=error.code)
                if error.diagnostic_code is not None:
                    observation["errorDiagnostic"] = error.diagnostic_code
            except asyncio.CancelledError:
                observation.update(state="interrupted", errorCode="evaluation_cancelled")
                raise
            finally:
                observation.update(
                    elapsedSeconds=round(time.monotonic() - started, 3),
                    providerRequests=len(recorder.records),
                    traces=recorder.records,
                )
                write_json(output, report)
            print(
                json.dumps({k: observation[k] for k in ("key", "state", "elapsedSeconds")}),
                flush=True,
            )
        report["status"] = "collected"
    except (Exception, asyncio.CancelledError):
        report["status"] = "interrupted"
        raise
    finally:
        report["finishedAt"] = datetime.now(UTC).isoformat()
        write_json(output, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provider-env", type=Path, required=True)
    parser.add_argument("--model-route", choices=("primary", "fallback"), default="fallback")
    parser.add_argument("--model-timeout-seconds", type=float, default=120)
    parser.add_argument("--reasoning-effort", choices=("low", "medium", "high", "xhigh"))
    parser.add_argument("--strict-output", action="store_true")
    args = parser.parse_args()
    settings = load_route_settings(args.provider_env, args.model_route)
    if args.reasoning_effort is not None:
        key = "fallback_reasoning_effort" if args.model_route == "fallback" else "reasoning_effort"
        settings = ModelSettings.model_validate(
            {**settings.model_dump(), key: args.reasoning_effort}
        )
    asyncio.run(
        collect(
            args.input,
            args.output,
            settings,
            model_route=args.model_route,
            model_timeout_seconds=args.model_timeout_seconds,
            strict_output=args.strict_output,
        ),
        loop_factory=selector_event_loop_factory,
    )


if __name__ == "__main__":
    main()
