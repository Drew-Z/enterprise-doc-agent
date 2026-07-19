from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _documents(path: Path) -> list[dict[str, object]]:
    payload = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    return [item for item in payload if isinstance(item, dict)]


def test_all_runtime_images_have_non_root_dockerfiles_and_no_floating_latest_tag() -> None:
    docker_dir = ROOT / "infra" / "docker"
    for name in ("api", "worker", "consumer", "web"):
        text = (docker_dir / f"Dockerfile.{name}").read_text(encoding="utf-8")
        assert "FROM " in text
        assert "USER " in text
        assert ":latest" not in text
        assert "10001" in text or name == "web"
        assert all("@sha256:" in line for line in text.splitlines() if line.startswith("FROM "))


def test_kubernetes_deployments_have_probes_resources_and_non_root_security() -> None:
    documents = _documents(ROOT / "infra/k8s/base/deployments.yaml")
    deployments = [item for item in documents if item.get("kind") == "Deployment"]
    assert {item["metadata"]["name"] for item in deployments} == {
        "enterprise-doc-api",
        "enterprise-doc-worker",
        "enterprise-doc-consumer",
        "enterprise-doc-web",
    }
    for deployment in deployments:
        pod = deployment["spec"]["template"]
        pod_security = pod["spec"]["securityContext"]
        assert pod_security["runAsNonRoot"] is True
        container = pod["spec"]["containers"][0]
        assert container["securityContext"]["allowPrivilegeEscalation"] is False
        assert "readinessProbe" in container
        assert "livenessProbe" in container
        assert "startupProbe" in container
        assert "requests" in container["resources"]
        assert "limits" in container["resources"]
        assert "@sha256:" in container["image"]


def test_migration_job_and_policies_are_present() -> None:
    migration = _documents(ROOT / "infra/k8s/base/migration-job.yaml")[0]
    assert migration["kind"] == "Job"
    assert migration["spec"]["template"]["spec"]["containers"][0]["command"] == [
        "alembic",
        "upgrade",
        "head",
    ]
    policy_docs = _documents(ROOT / "infra/k8s/base/network-policy.yaml")
    assert any(item["metadata"]["name"] == "enterprise-doc-default-deny" for item in policy_docs)
    assert any(item["metadata"]["name"] == "enterprise-doc-runtime-egress" for item in policy_docs)


def test_secret_example_is_not_referenced_as_a_real_secret() -> None:
    base = (ROOT / "infra/k8s/base/kustomization.yaml").read_text(encoding="utf-8")
    assert "secret.example.yaml" not in base
    secret = (ROOT / "infra/k8s/base/secret.example.yaml").read_text(encoding="utf-8")
    assert "replace-with-secret-manager-reference" in secret


def test_database_url_has_one_secret_backed_source() -> None:
    config = (ROOT / "infra/k8s/base/configmap.yaml").read_text(encoding="utf-8")
    secret = (ROOT / "infra/k8s/base/secret.example.yaml").read_text(encoding="utf-8")
    assert "DATABASE__URL" not in config
    assert "DATABASE__URL" in secret


def test_network_policy_and_prod_environment_are_explicit() -> None:
    policies = _documents(ROOT / "infra/k8s/base/network-policy.yaml")
    egress = next(
        item
        for item in policies
        if item["metadata"]["name"] == "enterprise-doc-runtime-egress"
    )
    ports = {
        port["port"]
        for rule in egress["spec"]["egress"]
        for port in rule.get("ports", [])
    }
    assert 443 in ports
    api_ingress = next(
        item
        for item in policies
        if item["metadata"]["name"] == "enterprise-doc-api-ingress"
    )
    assert all(
        "namespaceSelector" not in peer
        for rule in api_ingress["spec"]["ingress"]
        for peer in rule["from"]
    )
    prod_patch = (ROOT / "infra/k8s/overlays/prod/configmap-patch.yaml").read_text(
        encoding="utf-8"
    )
    assert "APP_ENV: production" in prod_patch


def test_ci_workflows_have_no_allow_failure_and_include_release_boundaries() -> None:
    workflow_dir = ROOT / ".github/workflows"
    container = (workflow_dir / "container.yml").read_text(encoding="utf-8")
    deploy = (workflow_dir / "deploy-staging.yml").read_text(encoding="utf-8")
    rollback = (workflow_dir / "rollback.yml").read_text(encoding="utf-8")
    assert "sbom" in container.lower()
    assert "trivy" in container.lower()
    assert 'exit-code: "1"' in container
    assert "cosign sign" in container
    assert "cosign verify" in container
    assert "continue-on-error" not in container
    assert "render_k8s_phase.py" in deploy
    assert "staging-prerequisites.yaml" in deploy
    assert "staging-migration.yaml" in deploy
    assert "staging-workloads.yaml" in deploy
    assert deploy.index("wait --for=condition=complete") < deploy.index(
        "Apply workloads after migration"
    )
    assert "rollout status" in deploy
    assert "scripts/rollback_release.py" in rollback
    assert "revisions_json" in rollback
    rollback_script = (ROOT / "scripts/rollback_release.py").read_text(encoding="utf-8")
    assert "--to-revision" in rollback_script
