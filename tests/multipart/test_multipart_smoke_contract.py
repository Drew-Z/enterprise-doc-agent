from __future__ import annotations

import hashlib
import socket
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

_SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "multipart_smoke.py"
_SPEC = spec_from_file_location("multipart_smoke_test_module", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
multipart_smoke = module_from_spec(_SPEC)
sys.modules[_SPEC.name] = multipart_smoke
sys.path.insert(0, str(_SCRIPT_PATH.parent))
_SPEC.loader.exec_module(multipart_smoke)

ApiRssSample = multipart_smoke.ApiRssSample
build_sanitized_report = multipart_smoke.build_sanitized_report
deterministic_bytes = multipart_smoke.deterministic_bytes
sha256_for_generated_content = multipart_smoke.sha256_for_generated_content
configured_smoke_ports = multipart_smoke.configured_smoke_ports
host_port_available = multipart_smoke.host_port_available


def test_generated_content_is_repeatable_and_matches_streamed_hash() -> None:
    size_bytes = 5 * 1024**2 + 17
    first = deterministic_bytes(offset=0, size_bytes=size_bytes)
    second = deterministic_bytes(offset=0, size_bytes=size_bytes)

    assert first == second
    assert b"\x00" not in first
    assert first.decode("utf-8")
    assert sha256_for_generated_content(size_bytes) == hashlib.sha256(first).hexdigest()


def test_sanitized_report_contains_measurements_without_sensitive_identifiers() -> None:
    report = build_sanitized_report(
        size_bytes=17 * 1024**2,
        part_size_bytes=16 * 1024**2,
        expected_part_count=2,
        interrupted_after_parts=1,
        observed_parts_before_resume=1,
        uploaded_parts_after_resume=1,
        first_completion_replayed=False,
        second_completion_replayed=True,
        rss_samples=(
            ApiRssSample(generation=1, elapsed_seconds=0.0, rss_bytes=100),
            ApiRssSample(generation=1, elapsed_seconds=0.5, rss_bytes=120),
            ApiRssSample(generation=2, elapsed_seconds=0.0, rss_bytes=110),
            ApiRssSample(generation=2, elapsed_seconds=0.5, rss_bytes=130),
        ),
        started_at="2026-07-18T00:00:00+00:00",
        completed_at="2026-07-18T00:01:00+00:00",
        duration_seconds=60.0,
        environment={"operating_system": "test", "architecture": "test"},
    )

    assert report["result"] == "passed"
    assert report["restart_count"] == 1
    assert report["completion_retry_replayed"] is True
    assert report["api_rss"]["sample_count"] == 4
    assert report["api_rss"]["max_bytes"] == 130
    assert report["api_rss"]["max_generation_delta_bytes"] == 20
    serialized = str(report).lower()
    for forbidden in (
        "token",
        "authorization",
        "signed_url",
        "object_key",
        "upload_id",
        "session_id",
        "filename",
        "sha256",
    ):
        assert forbidden not in serialized


def test_smoke_ports_follow_compose_host_port_overrides(monkeypatch) -> None:
    monkeypatch.setenv("POSTGRES_PORT", "15432")
    monkeypatch.setenv("REDIS_PORT", "16379")
    monkeypatch.setenv("MINIO_API_PORT", "19000")
    monkeypatch.setenv("MINIO_CONSOLE_PORT", "19001")

    assert {item.name: item.port for item in configured_smoke_ports()} == {
        "api": 8000,
        "postgres": 15432,
        "redis": 16379,
        "minio-api": 19000,
        "minio-console": 19001,
    }


def test_preflight_detects_a_bound_but_non_listening_port() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("127.0.0.1", 0))
        port = occupied.getsockname()[1]
        check = multipart_smoke.PortCheck("occupied", "127.0.0.1", port)

        assert host_port_available(check) is False
