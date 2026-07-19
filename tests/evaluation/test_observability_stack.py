from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_observability_profile_is_optional_and_scrapes_each_process() -> None:
    compose = yaml.safe_load(
        (ROOT / "infra/compose/docker-compose.yml").read_text(encoding="utf-8")
    )
    services = compose["services"]
    assert services["prometheus"]["profiles"] == ["observability"]
    assert services["grafana"]["profiles"] == ["observability"]

    prometheus = yaml.safe_load(
        (ROOT / "infra/observability/prometheus.yml").read_text(encoding="utf-8")
    )
    targets = {
        target
        for job in prometheus["scrape_configs"]
        for config in job["static_configs"]
        for target in config["targets"]
    }
    assert targets == {
        "host.docker.internal:8000",
        "host.docker.internal:8081",
        "host.docker.internal:8082",
    }


def test_local_dashboard_uses_only_bounded_metric_labels() -> None:
    dashboard = json.loads(
        (ROOT / "infra/observability/grafana/dashboards/enterprise-doc-agent.json").read_text(
            encoding="utf-8"
        )
    )
    rendered = json.dumps(dashboard)
    assert "tenant" not in rendered.lower()
    assert "run_id" not in rendered.lower()
    assert "document_id" not in rendered.lower()
    assert "enterprise_doc_api_requests_total" in rendered
    assert "enterprise_doc_worker_jobs_completed_total" in rendered
