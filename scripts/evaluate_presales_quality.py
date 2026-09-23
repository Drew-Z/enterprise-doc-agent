"""Bounded public-demo inference collection and separate, offline presales scoring."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from pydantic import Field, model_validator

from enterprise_doc_core.presales.schemas import (
    CreatePacket,
    PacketView,
    PresalesModel,
    RequirementInput,
    Status,
)


class Source(PresalesModel):
    key: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    filename: str = Field(pattern=r"^[A-Za-z0-9_-]+\.txt$")
    applicability: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=20000)


class Dataset(PresalesModel):
    schema_version: Literal["presales-quality-input-v1"]
    synthetic: Literal[True]
    provenance: str
    title: str = Field(min_length=1, max_length=160)
    sources: list[Source] = Field(min_length=1, max_length=6)
    requirements: list[RequirementInput] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def unique_keys(self) -> Dataset:
        for values in (
            [s.key for s in self.sources],
            [s.filename for s in self.sources],
            [r.key for r in self.requirements],
        ):
            if len(set(values)) != len(values):
                raise ValueError("duplicate_input_key")
        return self


class Anchor(PresalesModel):
    source_key: str
    excerpt: str = Field(min_length=1, max_length=600)


class Expected(PresalesModel):
    key: str
    status: Status
    required_evidence: list[Anchor]
    reference_answer: str
    review_points: list[str]


class Gold(PresalesModel):
    schema_version: Literal["presales-quality-gold-v1"]
    dataset_sha256: str
    review_status: str
    rows: list[Expected]


def load_dataset(path: Path) -> tuple[Dataset, str]:
    if path.stat().st_size > 256 * 1024:
        raise ValueError("dataset_too_large")
    raw = path.read_bytes()
    return Dataset.model_validate_json(raw), hashlib.sha256(raw).hexdigest()


def write_json(path: Path, payload: object, *, exclusive: bool = False) -> None:
    with path.open("x" if exclusive else "w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


class RequestFailure(ValueError):
    pass


def request(client: httpx.Client, method: str, path: str, **kwargs: Any) -> httpx.Response:
    # Exceptions and arbitrary upstream response bodies can contain credentials.
    try:
        response = client.request(method, path, **kwargs)
    except httpx.HTTPError as error:
        raise RequestFailure(type(error).__name__) from None
    if response.status_code not in {200, 201, 204}:
        raise RequestFailure(f"http_{response.status_code}")
    return response


def upload(
    client: httpx.Client, objects: httpx.Client, source: Source, object_hosts: tuple[str, ...]
) -> str:
    content = source.content.encode("utf-8")
    digest = hashlib.sha256(content)
    checksum = base64.b64encode(digest.digest()).decode("ascii")
    created = request(
        client,
        "POST",
        "/api/upload-sessions",
        headers={"Idempotency-Key": uuid4().hex},
        json={
            "filename": source.filename,
            "sizeBytes": len(content),
            "mediaType": "text/plain",
            "sha256": digest.hexdigest(),
        },
    ).json()
    path = f"/api/upload-sessions/{created['sessionId']}"
    signed = request(
        client,
        "POST",
        f"{path}/parts/1/presign",
        json={"sizeBytes": len(content), "checksumSha256": checksum},
    ).json()
    url = urlsplit(signed["url"])
    if url.scheme != "https" or url.hostname not in object_hosts or url.username or url.password:
        raise ValueError("object_host_not_allowed")
    headers = signed["headers"]
    if any(
        k.lower() in {"cookie", "authorization", "x-session-context", "x-csrf-token"}
        for k in headers
    ):
        raise ValueError("object_credentials_forbidden")
    # The object client never receives the browser's cookie or default headers.
    response = request(objects, "PUT", signed["url"], content=content, headers=headers)
    completed = request(
        client,
        "POST",
        f"{path}/complete",
        json={
            "parts": [
                {
                    "partNumber": 1,
                    "sizeBytes": len(content),
                    "etag": response.headers["etag"],
                    "checksumSha256": checksum,
                }
            ]
        },
    ).json()
    return str(completed["versionId"])


def wait_ready(client: httpx.Client, versions: set[str], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        documents = request(client, "GET", "/api/documents").json()
        owned = [d for d in documents if d["versionId"] in versions]
        if any(
            d["versionStatus"] == "failed" or d.get("ingestionStatus") == "failed" for d in owned
        ):
            raise ValueError("ingestion_failed")
        if versions == {d["versionId"] for d in owned if d["versionStatus"] == "ready"}:
            return
        time.sleep(2)
    raise ValueError("ingestion_timeout")


def collect(
    dataset_path: Path,
    output: Path,
    *,
    base_url: str,
    object_hosts: tuple[str, ...],
    repeats: int = 2,
    transport: httpx.BaseTransport | None = None,
    object_transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    dataset, digest = load_dataset(dataset_path)
    url = urlsplit(base_url)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in {"", "/"}
    ):
        raise ValueError("base_url_must_be_https_origin")
    if repeats not in {1, 2} or not object_hosts:
        raise ValueError("invalid_run_budget")
    report: dict[str, Any] = {
        "schemaVersion": "presales-quality-run-v1",
        "datasetSha256": digest,
        "runnerSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "baseUrl": base_url,
        "startedAt": datetime.now(UTC).isoformat(),
        "plannedRepeats": repeats,
        "maxGenerationAttempts": repeats * len(dataset.requirements),
        "automaticRetries": 0,
        "syntheticOnly": True,
        "runs": [],
        "status": "running",
    }
    write_json(output, report, exclusive=True)  # Refuse overwrites before any network request.
    try:
        for repeat in range(repeats):
            run: dict[str, Any] = {"repeat": repeat + 1, "versions": {}, "observations": []}
            report["runs"].append(run)
            write_json(output, report)
            with httpx.Client(
                base_url=base_url,
                transport=transport,
                follow_redirects=False,
                timeout=180,
                headers={"Origin": base_url.rstrip("/")},
            ) as client:
                try:
                    snapshot = request(client, "POST", "/auth/demo", json={}).json()
                    if snapshot.get("demo") is not True:
                        raise ValueError("not_a_demo_session")
                    client.headers.update(
                        {
                            "X-Session-Context": snapshot["contextVersion"],
                            "X-CSRF-Token": snapshot["csrfToken"],
                        }
                    )
                    run.update(
                        tenantId=snapshot["currentTenant"]["tenantId"],
                        expiresAt=snapshot["expiresAt"],
                    )
                    with httpx.Client(
                        transport=object_transport, follow_redirects=False, timeout=60
                    ) as objects:
                        for source in dataset.sources:
                            run["versions"][source.key] = upload(
                                client, objects, source, object_hosts
                            )
                            write_json(output, report)
                    wait_ready(client, set(run["versions"].values()), 180)
                    payload = CreatePacket(
                        title=dataset.title,
                        sources=[
                            {"versionId": run["versions"][s.key], "applicability": s.applicability}
                            for s in dataset.sources
                        ],
                        requirements=dataset.requirements,
                    ).model_dump(mode="json", by_alias=True)
                    packet = PacketView.model_validate(
                        request(
                            client,
                            "POST",
                            "/api/presales",
                            json=payload,
                            headers={"Idempotency-Key": uuid4().hex},
                        ).json()
                    )
                    path = f"/api/presales/{packet.id}"
                    run["packet"] = packet.model_dump(mode="json", by_alias=True)
                    write_json(output, report)
                    for row in packet.rows:
                        observation: dict[str, Any] = {
                            "key": row.requirement.key,
                            "startedAt": datetime.now(UTC).isoformat(),
                            "state": "dispatching",
                        }
                        run["observations"].append(observation)
                        write_json(output, report)
                        started = time.monotonic()
                        try:
                            response = client.post(
                                f"{path}/rows/{row.id}/generate",
                                json={},
                                headers={"Idempotency-Key": uuid4().hex},
                            )
                            observation["httpStatus"] = response.status_code
                        except httpx.HTTPError as error:
                            observation["transportError"] = type(error).__name__
                        observation["elapsedSeconds"] = round(time.monotonic() - started, 3)
                        observation["state"] = "observed"
                        write_json(output, report)
                        # A disconnected response is uncertain, so only read the existing row.
                        current = PacketView.model_validate(request(client, "GET", path).json())
                        run["packet"] = current.model_dump(mode="json", by_alias=True)
                        write_json(output, report)
                        latest = next(r for r in current.rows if r.id == row.id)
                        print(
                            json.dumps(
                                {
                                    "repeat": repeat + 1,
                                    "key": row.requirement.key,
                                    "state": latest.state,
                                    "seconds": observation["elapsedSeconds"],
                                }
                            ),
                            flush=True,
                        )
                        if latest.state == "running":
                            raise ValueError("generation_outcome_uncertain_stop")
                        if observation.get("httpStatus") in {401, 403, 429}:
                            raise ValueError("generation_admission_rejected_stop")
                    run["demoUsage"] = request(client, "GET", "/api/demo").json()
                finally:
                    if "X-Session-Context" in client.headers:
                        try:
                            request(client, "POST", "/auth/logout", json={})
                            run["signedOut"] = True
                        except RequestFailure as error:
                            run["signedOut"] = False
                            run["logoutError"] = str(error)
                    write_json(output, report)
        report["status"] = "collected"
    except Exception as error:
        report["status"] = "interrupted"
        report["error"] = str(error) if isinstance(error, RequestFailure) else type(error).__name__
        # Only our own small diagnostic codes are safe to retain.
        if type(error) is ValueError and re.fullmatch(r"[a-z_]{1,80}", str(error)):
            report["error"] = str(error)
        raise
    finally:
        report["finishedAt"] = datetime.now(UTC).isoformat()
        write_json(output, report)
    return report


def score(dataset_path: Path, gold_path: Path, report: dict[str, Any]) -> dict[str, Any]:
    dataset, digest = load_dataset(dataset_path)
    gold = Gold.model_validate_json(gold_path.read_bytes())
    if digest != report["datasetSha256"] or digest != gold.dataset_sha256:
        raise ValueError("dataset_hash_mismatch")
    if report["schemaVersion"] != "presales-quality-run-v1" or report["plannedRepeats"] not in {
        1,
        2,
    }:
        raise ValueError("invalid_report")
    requirements = {r.key: r for r in dataset.requirements}
    sources = {s.key: s for s in dataset.sources}
    expected = {r.key: r for r in gold.rows}
    if len(expected) != len(gold.rows) or set(expected) != set(requirements):
        raise ValueError("gold_coverage_mismatch")
    for target in gold.rows:
        if target.status != "insufficient_evidence" and not target.required_evidence:
            raise ValueError("gold_missing_evidence")
        if (
            target.status == "conflicting_evidence"
            and len({e.source_key for e in target.required_evidence}) < 2
        ):
            raise ValueError("gold_conflict_missing_side")
        if any(
            e.source_key not in sources or e.excerpt not in sources[e.source_key].content
            for e in target.required_evidence
        ):
            raise ValueError("invalid_gold_evidence")
    repeats = report["plannedRepeats"]
    if len(report["runs"]) > repeats:
        raise ValueError("unplanned_run")
    rows: list[dict[str, Any]] = []
    attempts = []
    latency = []
    for index in range(repeats):
        run = report["runs"][index] if index < len(report["runs"]) else {}
        packet = PacketView.model_validate(run["packet"]) if run.get("packet") else None
        versions = run.get("versions", {})
        reverse = {v: k for k, v in versions.items()}
        if packet:
            if set(versions) != set(sources) or len(reverse) != len(sources):
                raise ValueError("source_binding_mismatch")
            if {str(s.version_id) for s in packet.sources} != set(reverse):
                raise ValueError("packet_source_mismatch")
            for source in packet.sources:
                original = sources[reverse[str(source.version_id)]]
                if hashlib.sha256(original.content.encode()).hexdigest() != source.content_sha256:
                    raise ValueError("source_hash_mismatch")
                if source.applicability != original.applicability:
                    raise ValueError("source_scope_mismatch")
        actual = {r.requirement.key: r for r in packet.rows} if packet else {}
        if packet and (len(actual) != len(packet.rows) or set(actual) != set(requirements)):
            raise ValueError("packet_requirement_mismatch")
        for observation in run.get("observations", []):
            if observation.get("elapsedSeconds") is not None:
                latency.append(float(observation["elapsedSeconds"]))
        for key, target in expected.items():
            row = actual.get(key)
            if row and row.requirement != requirements[key]:
                raise ValueError("requirement_text_mismatch")
            draft = row.draft if row else None
            attempts.extend(row.attempts if row else [])
            valid = (
                [
                    c
                    for c in draft.citations
                    if str(c.document_version_id) in reverse
                    and c.excerpt in sources[reverse[str(c.document_version_id)]].content
                ]
                if draft
                else []
            )
            covered = sum(
                any(
                    reverse[str(c.document_version_id)] == anchor.source_key
                    and anchor.excerpt in c.excerpt
                    for c in valid
                )
                for anchor in target.required_evidence
            )
            predicted = draft.status if draft else None
            unsafe = (predicted == "supported" and target.status != "supported") or (
                predicted == "conditional"
                and target.status
                in {"contradicted", "insufficient_evidence", "conflicting_evidence"}
            )
            rows.append(
                {
                    "repeat": index + 1,
                    "key": key,
                    "expectedStatus": target.status,
                    "predictedStatus": predicted,
                    "statusMatch": predicted == target.status,
                    "unsafeAffirmative": unsafe,
                    "missingDraft": draft is None,
                    "citations": len(draft.citations) if draft else 0,
                    "validCitations": len(valid),
                    "requiredEvidence": len(target.required_evidence),
                    "coveredRequiredEvidence": covered,
                }
            )
    usage: dict[str, int | None] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        values = [a.usage.get(key) if a.usage else None for a in attempts]
        usage[key] = (
            sum(v for v in values if v is not None)
            if values and all(v is not None for v in values)
            else None
        )
    result: dict[str, Any] = {
        "schemaVersion": "presales-quality-score-v1",
        "datasetSha256": digest,
        "syntheticOnly": True,
        "independentDomainReview": False,
        "semanticReviewRequired": True,
        "expectedRows": repeats * len(requirements),
        "rows": rows,
        "usage": usage,
        "attemptsWithUsage": sum(a.usage is not None for a in attempts),
        "observedAttempts": len(attempts),
        "providerRequestCount": sum(
            a.provider_request_count for a in attempts if a.provider_request_count is not None
        )
        if attempts and all(a.provider_request_count is not None for a in attempts)
        else None,
        "costAmount": None,
        "costCurrency": None,
        "costReason": "No verified route price or bill",
        "latencySeconds": {
            "sampleCount": len(latency),
            "min": min(latency) if latency else None,
            "median": statistics.median(latency) if latency else None,
            "max": max(latency) if latency else None,
            "scope": "All observed generation HTTP attempts, including failures",
        },
    }
    for field, row_field in (
        ("statusMatches", "statusMatch"),
        ("missingDrafts", "missingDraft"),
        ("unsafeAffirmatives", "unsafeAffirmative"),
        ("citations", "citations"),
        ("validCitations", "validCitations"),
        ("requiredEvidence", "requiredEvidence"),
        ("coveredRequiredEvidence", "coveredRequiredEvidence"),
    ):
        result[field] = sum(row[row_field] for row in rows)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run", "score"])
    parser.add_argument("--input", type=Path, default=Path("evaluation/presales_quality_v1.json"))
    parser.add_argument(
        "--gold", type=Path, default=Path("evaluation/presales_quality_v1.gold.json")
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run", type=Path)
    parser.add_argument("--base-url")
    parser.add_argument("--object-host", action="append", default=[])
    parser.add_argument("--repeats", type=int, choices=[1, 2], default=2)
    args = parser.parse_args()
    try:
        if args.command == "run":
            if not args.base_url:
                raise ValueError("base_url_required")
            collect(
                args.input,
                args.output,
                base_url=args.base_url,
                object_hosts=tuple(args.object_host),
                repeats=args.repeats,
            )
        else:
            if not args.run:
                raise ValueError("run_required")
            result = score(args.input, args.gold, json.loads(args.run.read_bytes()))
            write_json(args.output, result, exclusive=True)
        print(json.dumps({"status": "completed", "output": str(args.output)}))
        return 0
    except (ValueError, OSError, KeyError, httpx.HTTPError) as error:
        print(json.dumps({"status": "failed", "errorType": type(error).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
