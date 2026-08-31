from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_embedding_rollout_job_has_capacity_for_current_4c4g_backlog() -> None:
    source = (ROOT / "infra/k8s/overlays/single-node-4c4g/embedding-rollout-patch.yaml").read_text(
        encoding="utf-8"
    )
    assert "activeDeadlineSeconds: 3060" in source
    assert '- "3000"' in source
    guardrails = (ROOT / "infra/k8s/bootstrap/staging-deployer-guardrails.yaml").read_text(
        encoding="utf-8"
    )
    assert "'--deadline-seconds', '3000'" in guardrails
    assert "'--deadline-seconds', '1200'" in guardrails
    assert "== 'single-node-4c4g'" in guardrails


def test_4c4g_embedding_gate_keeps_queue_workers_and_scales_consumer() -> None:
    source = (ROOT / ".github/workflows/deploy-staging.yml").read_text(encoding="utf-8")
    block = source[source.index("Run embedding provider and reindex gate") :]
    profile_branch = block.split(
        'if test "${DEPLOYMENT_PROFILE:-}" = "single-node-4c4g"', 1
    )[1].split("else", 1)[0]
    assert "deployment/enterprise-doc-worker" not in profile_branch
    assert "deployment/enterprise-doc-consumer --replicas=4" in profile_branch
    assert "selector='app.kubernetes.io/name in (enterprise-doc-api,enterprise-doc-web)'" in profile_branch
    assert "deployment/enterprise-doc-worker" in source
    assert "deployment/enterprise-doc-consumer" in source
    assert "deployment/enterprise-doc-web --replicas=0" in source
