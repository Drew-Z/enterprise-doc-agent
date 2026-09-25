from __future__ import annotations

import asyncio
import hashlib
import json
from uuid import uuid4

import httpx
import pytest


@pytest.mark.parametrize("version", [True, 1.0, "1"])
def test_plan_version_requires_an_integer(version):
    from pydantic import ValidationError
    from scripts.business_capacity import BusinessPlan

    payload = plan_payload()
    payload["schema_version"] = version
    with pytest.raises(ValidationError):
        BusinessPlan.model_validate(payload)


async def test_cancellation_retains_active_and_unsubmitted_samples(tmp_path):
    from scripts.business_capacity import load_business_plan, run_business_matrix

    (tmp_path / "source.txt").write_bytes(b"Retention is 30 days.")
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan_payload()), encoding="utf-8")
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def suspended(request):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    journal = []
    task = asyncio.create_task(
        run_business_matrix(
            load_business_plan(path),
            "test-only",
            object(),
            api_transport=httpx.MockTransport(suspended),
            on_sample=journal.append,
        )
    )
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    report = await asyncio.wait_for(task, 2)
    assert cancelled.is_set()
    assert report["status"] == "interrupted"
    assert report["summary"]["planned_tasks"] == 8
    assert report["summary"]["not_run"] == 7
    assert report["samples"][0]["status"] == "interrupted"
    assert len(journal) == 8
    assert len({s["task_index"] for s in journal}) == 8
    assert report["production_capacity_approved"] is False


def plan_payload() -> dict:
    return {
        "schema_version": 1,
        "base_url": "http://127.0.0.1:18766",
        "object_origins": ["http://127.0.0.1:9000"],
        "repetitions": 2,
        "phases": [
            {"name": name, "tasks": 1, "concurrency": 1}
            for name in ("ramp", "steady_state", "burst", "recovery")
        ],
        "cases": [
            {
                "key": "txt",
                "path": "source.txt",
                "sha256": hashlib.sha256(b"Retention is 30 days.").hexdigest(),
                "size_bytes": 21,
                "media_type": "text/plain",
                "query": "Retention",
                "excerpt": "Retention is 30 days.",
            }
        ],
    }


def test_business_plan_freezes_inputs_and_rejects_changed_files_before_execution(tmp_path):
    from scripts.business_capacity import load_business_plan

    content = b"Retention is 30 days."
    (tmp_path / "source.txt").write_bytes(content)
    payload = plan_payload()
    payload["cases"][0]["size_bytes"] = len(content)
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = load_business_plan(path)
    assert loaded.plan.total_tasks == 8
    assert loaded.cases[0].content == content
    (tmp_path / "source.txt").write_bytes(b"Retention is 90 days.")
    with pytest.raises(ValueError, match="fixture_integrity"):
        load_business_plan(path)


def test_committed_example_has_reproducible_fixture_bytes():
    from scripts.business_capacity import load_business_plan
    from scripts.run_business_capacity import ROOT

    loaded = load_business_plan(ROOT / "infra/capacity/business-capacity.example.json")
    assert loaded.plan.total_tasks == 10
    assert loaded.cases[0].spec.excerpt.encode() in loaded.cases[0].content


@pytest.mark.parametrize("invalid_signature", [None, "origin", "header", "redirect"])
async def test_upload_samples_real_multipart_protocol_without_leaking_api_credentials(
    invalid_signature,
):
    from scripts.business_capacity import (
        BusinessCase,
        BusinessFailure,
        BusinessIO,
        BusinessPlan,
        LoadedCase,
        upload_case,
    )

    payload = plan_payload()
    content = b"Retention is 30 days."
    payload["cases"][0]["size_bytes"] = len(content)
    plan = BusinessPlan.model_validate(payload)
    case = LoadedCase(BusinessCase.model_validate(payload["cases"][0]), content)
    session, document, version = (str(uuid4()) for _ in range(3))
    puts, completed, methods = [], [], []

    def api(request):
        methods.append((request.method, request.url.path))
        assert request.headers["authorization"] == "Bearer local-test-secret"
        data = json.loads(request.content)
        if request.url.path == "/api/upload-sessions":
            return httpx.Response(
                201,
                json={
                    "sessionId": session,
                    "status": "active",
                    "filename": "source.txt",
                    "extension": ".txt",
                    "mediaType": "text/plain",
                    "sizeBytes": len(content),
                    "declaredSha256": case.spec.sha256,
                    "partSizeBytes": 11,
                    "expectedPartCount": 2,
                    "expiresAt": "2026-09-26T00:00:00Z",
                    "replayed": False,
                },
            )
        if request.url.path.endswith("/presign"):
            number = int(request.url.path.split("/")[-2])
            return httpx.Response(
                200,
                json={
                    "partNumber": number,
                    **data,
                    "url": f"http://127.0.0.1:{9001 if invalid_signature == 'origin' else 9000}"
                    f"/part/{number}?signature=secret",
                    "headers": {"Authorization": "secret"}
                    if invalid_signature == "header"
                    else {"x-amz-checksum-sha256": data["checksumSha256"]},
                    "expiresInSeconds": 60,
                },
            )
        completed.extend(data["parts"])
        return httpx.Response(
            200,
            json={
                "sessionId": session,
                "status": "completed",
                "documentId": document,
                "versionId": version,
                "completedAt": "2026-09-25T10:00:00Z",
                "replayed": False,
            },
        )

    def objects(request):
        assert "authorization" not in request.headers and "cookie" not in request.headers
        puts.append(request.content)
        if invalid_signature == "redirect":
            return httpx.Response(307, headers={"location": "https://redirect.invalid/secret"})
        return httpx.Response(200, headers={"etag": f'"part-{len(puts)}"'})

    io = BusinessIO(
        plan,
        "local-test-secret",
        api_transport=httpx.MockTransport(api),
        object_transport=httpx.MockTransport(objects),
    )
    refs = {}
    if invalid_signature:
        with pytest.raises(
            BusinessFailure,
            match="http_307" if invalid_signature == "redirect" else "object_request_rejected",
        ):
            await upload_case(io, case, "same-business-key", refs)
        assert len(puts) == int(invalid_signature == "redirect")
        assert not completed
        return
    receipt = await upload_case(io, case, "same-business-key", refs)
    assert str(receipt.version_id) == version
    assert b"".join(puts) == content
    assert [p["partNumber"] for p in completed] == [1, 2]
    assert io.requests == 6
    assert refs == {"session_id": session, "version_id": version, "document_id": document}


async def test_failed_upload_keeps_every_planned_task_and_downstream_boundary(tmp_path):
    from scripts.business_capacity import load_business_plan, run_business_matrix

    class NeverObserve:
        async def ingestion(self, *args):
            pytest.fail("failed upload must not reach database/object observation")

        async def retrieve(self, *args):
            pytest.fail("failed upload must not start retrieval")

        async def ledger(self, *args):
            pytest.fail("failed upload must not inspect a generation ledger")

    (tmp_path / "source.txt").write_bytes(b"Retention is 30 days.")
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan_payload()), encoding="utf-8")
    requests = []

    def failed(request):
        requests.append(request)
        return httpx.Response(503, text="secret-upstream-error")

    report = await run_business_matrix(
        load_business_plan(path),
        "secret-token",
        NeverObserve(),
        api_transport=httpx.MockTransport(failed),
    )
    assert report["status"] == "local_checks_failed"
    assert len(report["samples"]) == 8 and len(requests) == 8
    assert report["summary"]["business_successes"] == 0
    assert report["summary"]["planned_tasks"] == 8
    assert report["summary"]["boundaries"]["upload"]["failed"] == 8
    assert report["summary"]["boundaries"]["retrieval"]["not_run"] == 8
    assert report["production_capacity_approved"] is False
    assert "secret" not in json.dumps(report)


async def test_http_budget_stops_unsubmitted_tasks_without_removing_them(tmp_path):
    from scripts.business_capacity import load_business_plan, run_business_matrix

    payload = plan_payload()
    payload["max_http_requests"] = 1
    (tmp_path / "source.txt").write_bytes(b"Retention is 30 days.")
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    report = await run_business_matrix(
        load_business_plan(path),
        "test-only",
        object(),
        api_transport=httpx.MockTransport(lambda _: httpx.Response(503)),
    )
    assert report["http_requests"] == 1
    assert report["summary"]["not_run"] == 7
    assert report["summary"]["planned_tasks"] == 8


def test_business_cli_validates_without_credentials_and_preserves_existing_output(tmp_path):
    import subprocess
    import sys

    (tmp_path / "source.txt").write_bytes(b"Retention is 30 days.")
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan_payload()), encoding="utf-8")
    command = [
        sys.executable,
        "-B",
        "-X",
        "utf8",
        "-m",
        "scripts.run_business_capacity",
        "--plan",
        str(path),
    ]
    checked = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert checked.returncode == 0, checked.stderr
    assert json.loads(checked.stdout)["dry_run"] is True
    protected = tmp_path / "protected"
    protected.mkdir()
    (protected / "sentinel").write_bytes(b"keep")
    refused = subprocess.run(
        [
            *command,
            "--execute-local",
            "--confirm-controlled-target",
            "--output-dir",
            str(protected),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )
    assert refused.returncode == 2
    assert "output_directory_rejected" in refused.stderr
    assert (protected / "sentinel").read_bytes() == b"keep"


@pytest.mark.parametrize(
    "field,value",
    [
        ("base_url", "https://agent.example.com"),
        ("base_url", "http://secret@127.0.0.1:80"),
        ("max_run_seconds", float("inf")),
        ("poll_seconds", float("nan")),
        ("max_http_requests", True),
        ("max_http_requests", 20001),
        (
            "phases",
            [
                {"name": n, "tasks": 21, "concurrency": 1}
                for n in ("ramp", "steady_state", "burst", "recovery")
            ],
        ),
        (
            "phases",
            [
                {"name": n, "tasks": 1, "concurrency": 5}
                for n in ("recovery", "burst", "steady_state", "ramp")
            ],
        ),
    ],
)
def test_plan_rejects_unbounded_or_remote_inputs(field, value):
    from pydantic import ValidationError
    from scripts.business_capacity import BusinessPlan

    payload = plan_payload()
    payload[field] = value
    with pytest.raises(ValidationError):
        BusinessPlan.model_validate(payload)


@pytest.mark.parametrize(
    "deadline", ["request_timeout_seconds", "task_timeout_seconds", "max_run_seconds"]
)
async def test_deadlines_bound_slow_response_streams_and_keep_denominator(tmp_path, deadline):
    from scripts.business_capacity import load_business_plan, run_business_matrix

    closed = []

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"partial"
            await asyncio.Event().wait()

        async def aclose(self):
            closed.append(True)

    payload = plan_payload()
    payload[deadline] = 0.025
    (tmp_path / "source.txt").write_bytes(b"Retention is 30 days.")
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    report = await asyncio.wait_for(
        run_business_matrix(
            load_business_plan(path),
            "test-only",
            object(),
            api_transport=httpx.MockTransport(lambda _: httpx.Response(201, stream=SlowStream())),
        ),
        3,
    )
    assert report["status"] == "local_checks_failed"
    assert report["summary"]["planned_tasks"] == 8
    assert report["summary"]["business_non_successes"] == 8
    assert report["summary"]["boundaries"]["retrieval"]["not_run"] == 8
    assert len(closed) == report["http_requests"]
    assert report["samples"][0]["reason"] == (
        "request_timeout" if deadline == "request_timeout_seconds" else "boundary_timeout"
    )
    if deadline == "max_run_seconds":
        assert report["summary"]["not_run"] == 7


async def test_cli_resource_failure_preserves_sanitized_terminal_report(tmp_path, monkeypatch):
    import scripts.run_business_capacity as command
    from scripts.business_capacity import load_business_plan
    from scripts.run_business_capacity import execute_local

    from enterprise_doc_api.config import ApiSettings

    (tmp_path / "source.txt").write_bytes(b"Retention is 30 days.")
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan_payload()), encoding="utf-8")
    monkeypatch.setenv("ENTERPRISE_DOC_LOAD_TOKEN", "local-test-secret")

    def unavailable(settings):
        raise RuntimeError("secret-connection-detail")

    monkeypatch.setattr(command, "build_foundation_resources", unavailable)
    output = tmp_path / "output"
    report = await execute_local(load_business_plan(path), output, ApiSettings(_env_file=None))
    saved = json.loads((output / "run.json").read_text(encoding="utf-8"))
    assert saved == report
    assert saved["status"] == "interrupted"
    assert saved["planned_tasks"] == 8
    assert saved["completed_at"]
    assert "secret" not in json.dumps(saved)
