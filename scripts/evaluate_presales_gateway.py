"""Single-pass synthetic generation evaluation; no upload, retrieval or tenant writes.

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
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import httpx
from dotenv import dotenv_values
from pydantic import SecretStr

from enterprise_doc_core.config import ModelProvider, ModelSettings
from enterprise_doc_core.db import selector_event_loop_factory
from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.gateway import OpenAICompatiblePresalesGateway
from enterprise_doc_core.presales.schemas import GenerationInput, SourceSnapshot
from enterprise_doc_core.presales.settings import PresalesSettings
from scripts.evaluate_presales_quality import load_dataset, write_json


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner = inner
        self.records: list[dict[str, Any]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        record: dict[str, Any] = {"requestDispatched": True}
        self.records.append(record)
        # Only synthetic model input and bounded output, never headers or credentials.
        record["input"] = json.loads(json.loads(request.content)["messages"][1]["content"])
        phase = "awaiting_response_headers"
        try:
            response = await self.inner.handle_async_request(request)
            record["httpStatus"] = response.status_code
            phase = "reading_response_body"
            body = bytearray()
            try:
                async for part in response.aiter_bytes():
                    if len(body) + len(part) > 128 * 1024:
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
                raw = json.loads(body)
                record["response"] = {
                    k: raw[k] for k in ("id", "model", "choices", "usage") if k in raw
                }
            except (ValueError, TypeError):
                record["invalidJson"] = True
        return httpx.Response(response.status_code, content=bytes(body), request=request)

    async def aclose(self) -> None:
        await self.inner.aclose()


async def collect(
    dataset_path: Path,
    output: Path,
    settings: ModelSettings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    dataset, digest = load_dataset(dataset_path)
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
    report: dict[str, Any] = {
        "schemaVersion": "presales-gateway-run-v1",
        "scope": "generation_only_with_complete_synthetic_sources; no_retrieval_or_persistence",
        "datasetSha256": digest,
        "runnerSha256": hashlib.sha256(
            await asyncio.to_thread(Path(__file__).read_bytes)
        ).hexdigest(),
        "startedAt": datetime.now(UTC).isoformat(),
        "maxGenerationAttempts": len(dataset.requirements),
        "automaticRetries": 0,
        "versions": {
            s.key: str(v.version_id) for s, v in zip(dataset.sources, snapshots, strict=True)
        },
        "observations": [],
        "status": "running",
    }
    write_json(output, report, exclusive=True)
    try:
        for requirement in dataset.requirements:
            recorder = RecordingTransport(transport or httpx.AsyncHTTPTransport(retries=0))
            gateway = OpenAICompatiblePresalesGateway(
                settings,
                presales_settings=PresalesSettings(
                    model_route="fallback", model_timeout_seconds=120, row_timeout_seconds=150
                ),
                transport=recorder,
            )
            observation: dict[str, Any] = {
                "key": requirement.key,
                "startedAt": datetime.now(UTC).isoformat(),
                "state": "dispatching",
                "provenance": gateway.provenance,
            }
            report["observations"].append(observation)
            write_json(output, report)
            started = time.monotonic()
            try:
                result = await gateway.generate(
                    GenerationInput(requirement=requirement, sources=snapshots, evidence=evidence)
                )
                observation.update(
                    state="succeeded", result=result.model_dump(mode="json", by_alias=True)
                )
            except PresalesError as error:
                observation.update(state="failed", errorCode=error.code)
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
    args = parser.parse_args()
    values = dotenv_values(args.provider_env)
    settings = ModelSettings(
        fallback_provider=ModelProvider.OPENAI_COMPATIBLE,
        fallback_base_url=values["FALLBACK_BASE_URL"],
        fallback_api_key=SecretStr(values["FALLBACK_API_KEY"] or ""),
        fallback_model_name=values["FALLBACK_MODEL_NAME"],
        fallback_timeout_seconds=120,
    )
    asyncio.run(
        collect(args.input, args.output, settings), loop_factory=selector_event_loop_factory
    )


if __name__ == "__main__":
    main()
