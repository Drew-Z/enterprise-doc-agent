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
    assert services["otel-collector"]["profiles"] == ["observability"]
    assert ":latest" not in services["otel-collector"]["image"]

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


def test_local_otel_collector_redacts_principal_and_request_attributes() -> None:
    collector = yaml.safe_load(
        (ROOT / "infra/observability/otel-collector-config.yaml").read_text(encoding="utf-8")
    )
    actions = collector["processors"]["attributes/privacy"]["actions"]
    deleted = {action["key"] for action in actions if action.get("action") == "delete"}
    assert {
        "app.tenant_id",
        "app.actor_id",
        "app.request_id",
        "app.correlation_id",
        "http.request.header.authorization",
    } <= deleted
    assert collector["service"]["pipelines"]["traces"]["exporters"] == ["debug"]


def test_prometheus_recording_and_alert_rules_are_structured() -> None:
    prometheus = yaml.safe_load(
        (ROOT / "infra/observability/prometheus.yml").read_text(encoding="utf-8")
    )
    assert prometheus["rule_files"] == ["/etc/prometheus/rules/*.yml"]
    rules = yaml.safe_load(
        (ROOT / "infra/observability/rules/enterprise-doc-agent.rules.yml").read_text(
            encoding="utf-8"
        )
    )
    groups = {group["name"]: group for group in rules["groups"]}
    recordings = {rule["record"] for rule in groups["enterprise-doc-agent-recordings"]["rules"]}
    alerts = {rule["alert"] for rule in groups["enterprise-doc-agent-alerts"]["rules"]}
    assert "enterprise_doc:api_error_ratio5m" in recordings
    assert {
        "EnterpriseDocApiHighErrorRate",
        "EnterpriseDocWorkerFailureRate",
        "EnterpriseDocOutboxPublishErrors",
        "EnterpriseDocDependencyDown",
        "EnterpriseDocMetricsTargetDown",
        "EnterpriseDocMetricsTargetAbsent",
    } <= alerts
    rendered_rules = yaml.safe_dump(rules)
    assert "clamp_min(sum(rate(enterprise_doc_api_requests_total[5m])), 1e-9)" in rendered_rules
    assert "runbooks.example.invalid" not in rendered_rules


def test_kubernetes_prometheus_is_internal_and_bounded() -> None:
    prometheus = yaml.safe_load(
        (ROOT / "infra/observability/prometheus-kubernetes.yml").read_text(encoding="utf-8")
    )
    targets = {
        job["job_name"]: (
            job["dns_sd_configs"][0]["names"],
            job["dns_sd_configs"][0]["port"],
        )
        for job in prometheus["scrape_configs"]
    }
    assert targets == {
        "enterprise-doc-api": (
            ["enterprise-doc-api-metrics.enterprise-doc-agent-staging.svc.cluster.local"],
            8000,
        ),
        "enterprise-doc-worker": (
            ["enterprise-doc-worker-metrics.enterprise-doc-agent-staging.svc.cluster.local"],
            8081,
        ),
        "enterprise-doc-consumer": (
            ["enterprise-doc-consumer-metrics.enterprise-doc-agent-staging.svc.cluster.local"],
            8082,
        ),
    }
    documents = list(
        yaml.safe_load_all(
            (ROOT / "infra/observability/prometheus-kubernetes.yaml").read_text(encoding="utf-8")
        )
    )
    service = next(
        item
        for item in documents
        if item["kind"] == "Service" and item["metadata"]["name"] == "enterprise-doc-prometheus"
    )
    assert service["spec"]["type"] == "ClusterIP"
    headless_services = {
        item["metadata"]["name"]: item
        for item in documents
        if item["kind"] == "Service" and item["spec"].get("clusterIP") == "None"
    }
    assert set(headless_services) == {
        "enterprise-doc-api-metrics",
        "enterprise-doc-worker-metrics",
        "enterprise-doc-consumer-metrics",
    }
    assert all(item["spec"]["type"] == "ClusterIP" for item in headless_services.values())
    pvc = next(item for item in documents if item["kind"] == "PersistentVolumeClaim")
    assert pvc["spec"]["storageClassName"] == "local-path"
    deployment = next(item for item in documents if item["kind"] == "Deployment")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert "@sha256:" in container["image"]
    assert container["args"][-2:] == [
        "--storage.tsdb.retention.size=4GB",
        "--web.listen-address=0.0.0.0:9090",
    ]


def test_kubernetes_defaults_to_fail_closed_when_no_managed_collector_exists() -> None:
    configmap = yaml.safe_load((ROOT / "infra/k8s/base/configmap.yaml").read_text(encoding="utf-8"))
    assert configmap["data"]["OTEL__ENABLED"] == "false"


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
