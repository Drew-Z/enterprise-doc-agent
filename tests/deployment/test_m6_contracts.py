from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ACTION_SHA = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def _documents(path: Path) -> list[dict[str, object]]:
    payload = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    return [item for item in payload if isinstance(item, dict)]


def _render_kustomization(path: Path) -> list[dict[str, object]]:
    kustomize = shutil.which("kustomize")
    assert kustomize is not None, "standalone kustomize is required to validate overlays"
    completed = subprocess.run(
        [kustomize, "build", str(path)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = list(yaml.safe_load_all(completed.stdout))
    return [item for item in payload if isinstance(item, dict)]


def _named_resource(documents: list[dict[str, object]], kind: str, name: str) -> dict[str, object]:
    return next(
        item for item in documents if item.get("kind") == kind and item["metadata"]["name"] == name
    )


def _memory_mib(value: str) -> int:
    assert value.endswith("Mi"), value
    return int(value.removesuffix("Mi"))


def _cpu_millicores(value: str) -> int:
    if value.endswith("m"):
        return int(value.removesuffix("m"))
    return int(value) * 1000


def _workflow_steps(name: str) -> tuple[dict[str, object], list[dict[str, object]]]:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    images = jobs["images"]
    assert isinstance(images, dict)
    steps = images["steps"]
    assert isinstance(steps, list)
    return workflow, [step for step in steps if isinstance(step, dict)]


def _named_step(steps: list[dict[str, object]], name: str) -> dict[str, object]:
    return next(step for step in steps if step.get("name") == name)


def test_all_runtime_images_have_non_root_dockerfiles_and_no_floating_latest_tag() -> None:
    docker_dir = ROOT / "infra" / "docker"
    for name in ("api", "worker", "consumer", "web"):
        text = (docker_dir / f"Dockerfile.{name}").read_text(encoding="utf-8")
        assert "FROM " in text
        assert "USER " in text
        assert ":latest" not in text
        assert "10001" in text or name == "web"
        assert all("@sha256:" in line for line in text.splitlines() if line.startswith("FROM "))


def test_web_image_receives_explicit_object_store_origin_build_configuration() -> None:
    dockerfile = (ROOT / "infra" / "docker" / "Dockerfile.web").read_text(encoding="utf-8")
    assert "ARG VITE_OBJECT_STORE_ORIGINS=" in dockerfile
    assert "ENV VITE_OBJECT_STORE_ORIGINS=$VITE_OBJECT_STORE_ORIGINS" in dockerfile

    _, steps = _workflow_steps("container.yml")
    release_validation = _named_step(steps, "Validate Web release configuration")
    validation_command = str(release_validation["run"])
    assert "urlsplit" in validation_command
    assert "exact HTTPS origin" in validation_command
    local_build = _named_step(steps, "Build image for contract and scan")
    release_build = _named_step(steps, "Push immutable release image")
    for step in (local_build, release_build):
        settings = step["with"]
        assert isinstance(settings, dict)
        assert "VITE_OBJECT_STORE_ORIGINS=" in str(settings["build-args"])


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
    assert migration["spec"]["activeDeadlineSeconds"] == 300
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


def test_staging_overlay_defines_https_ingress_and_private_registry_contract() -> None:
    overlay = ROOT / "infra" / "k8s" / "overlays" / "staging"
    kustomization = (overlay / "kustomization.yaml").read_text(encoding="utf-8")
    assert "ingress.yaml" in kustomization
    assert "web-ingress-policy.yaml" in kustomization
    assert "smoke-api-ingress-policy.yaml" in kustomization
    assert "configmap-patch.yaml" in kustomization
    assert "image-pull-secret-patch.yaml" in kustomization

    ingress = _documents(overlay / "ingress.yaml")[0]
    assert ingress["kind"] == "Ingress"
    assert ingress["spec"]["tls"][0]["secretName"] == "enterprise-doc-staging-tls"
    assert ingress["spec"]["rules"][0]["host"] == "staging.example.invalid"

    pull_patches = _documents(overlay / "image-pull-secret-patch.yaml")
    assert {(item["kind"], item["metadata"]["name"]) for item in pull_patches} == {
        ("Job", "enterprise-doc-migrate"),
        ("Deployment", "enterprise-doc-api"),
        ("Deployment", "enterprise-doc-worker"),
        ("Deployment", "enterprise-doc-consumer"),
        ("Deployment", "enterprise-doc-web"),
    }
    assert {item["metadata"]["name"] for item in pull_patches} == {
        "enterprise-doc-migrate",
        "enterprise-doc-api",
        "enterprise-doc-worker",
        "enterprise-doc-consumer",
        "enterprise-doc-web",
    }
    assert all(
        item["spec"]["template"]["spec"]["imagePullSecrets"]
        == [{"name": "enterprise-doc-registry"}]
        for item in pull_patches
    )

    documents = _render_kustomization(overlay)
    smoke_ingress = _named_resource(
        documents,
        "NetworkPolicy",
        "enterprise-doc-staging-smoke-api-ingress",
    )
    assert smoke_ingress["spec"] == {
        "podSelector": {
            "matchLabels": {"app.kubernetes.io/name": "enterprise-doc-api"},
        },
        "policyTypes": ["Ingress"],
        "ingress": [
            {
                "from": [
                    {
                        "podSelector": {
                            "matchLabels": {
                                "app.kubernetes.io/name": "m5-staging-smoke",
                            },
                        },
                    },
                ],
                "ports": [{"protocol": "TCP", "port": 8000}],
            },
        ],
    }


def test_tiny_single_node_overlay_renders_with_a_bounded_k3s_runtime() -> None:
    overlay = ROOT / "infra" / "k8s" / "overlays" / "tiny-single-node"
    documents = _render_kustomization(overlay)

    deployments = [item for item in documents if item.get("kind") == "Deployment"]
    deployment_names = {item["metadata"]["name"] for item in deployments}
    assert deployment_names == {
        "enterprise-doc-api",
        "enterprise-doc-worker",
        "enterprise-doc-consumer",
        "enterprise-doc-web",
        "enterprise-doc-redis",
    }
    assert all(item["spec"]["replicas"] == 1 for item in deployments)
    assert not [item for item in documents if item.get("kind") == "PodDisruptionBudget"]

    workload_containers = [
        item["spec"]["template"]["spec"]["containers"][0] for item in deployments
    ]
    migration = _named_resource(documents, "Job", "enterprise-doc-migrate")
    migration_container = migration["spec"]["template"]["spec"]["containers"][0]
    peak_containers = [*workload_containers, migration_container]
    total_memory_limit = sum(
        _memory_mib(str(container["resources"]["limits"]["memory"]))
        for container in peak_containers
    )
    total_cpu_limit = sum(
        _cpu_millicores(str(container["resources"]["limits"]["cpu"]))
        for container in peak_containers
    )
    assert total_memory_limit <= 1024
    assert total_cpu_limit <= 1750
    for deployment in deployments:
        if deployment["metadata"]["name"] == "enterprise-doc-redis":
            assert deployment["spec"]["strategy"]["type"] == "Recreate"
        else:
            assert deployment["spec"]["strategy"] == {
                "type": "RollingUpdate",
                "rollingUpdate": {"maxSurge": 0, "maxUnavailable": 1},
            }

    ingress = _named_resource(documents, "Ingress", "enterprise-doc-web")
    assert ingress["spec"]["ingressClassName"] == "traefik"
    assert "nginx.ingress.kubernetes.io/force-ssl-redirect" not in ingress["metadata"].get(
        "annotations", {}
    )

    config = _named_resource(documents, "ConfigMap", "enterprise-doc-config")
    assert config["data"]["REDIS__URL"] == "redis://enterprise-doc-redis:6379/0"
    assert str(config["data"]["OBJECT_STORE__ENDPOINT"]).startswith("https://")
    assert not [
        item
        for item in documents
        if item.get("kind") in {"Deployment", "StatefulSet"}
        and item["metadata"]["name"] in {"postgres", "minio"}
    ]


def test_tiny_single_node_redis_and_network_boundaries_are_explicit() -> None:
    documents = _render_kustomization(ROOT / "infra" / "k8s" / "overlays" / "tiny-single-node")
    redis = _named_resource(documents, "Deployment", "enterprise-doc-redis")
    pod = redis["spec"]["template"]["spec"]
    container = pod["containers"][0]
    assert "@sha256:" in container["image"]
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert "--maxmemory-policy" in container["args"]
    assert "noeviction" in container["args"]
    assert "readinessProbe" in container
    assert "livenessProbe" in container
    assert container["resources"]["limits"]["memory"] == "96Mi"
    assert pod["volumes"] == [{"name": "data", "emptyDir": {}}]

    redis_ingress = _named_resource(documents, "NetworkPolicy", "enterprise-doc-redis-ingress")
    allowed_names = {
        peer["podSelector"]["matchLabels"]["app.kubernetes.io/name"]
        for rule in redis_ingress["spec"]["ingress"]
        for peer in rule["from"]
    }
    assert allowed_names == {
        "enterprise-doc-api",
        "enterprise-doc-worker",
        "enterprise-doc-consumer",
    }
    external_db = _named_resource(
        documents, "NetworkPolicy", "enterprise-doc-external-postgres-egress"
    )
    assert external_db["spec"]["podSelector"] == {
        "matchExpressions": [
            {
                "key": "app.kubernetes.io/name",
                "operator": "In",
                "values": [
                    "enterprise-doc-api",
                    "enterprise-doc-worker",
                    "enterprise-doc-consumer",
                    "enterprise-doc-migrate",
                ],
            },
        ],
    }
    assert external_db["spec"]["egress"] == [
        {
            "to": [{"ipBlock": {"cidr": "192.0.2.1/32"}}],
            "ports": [{"protocol": "TCP", "port": 5432}],
        },
    ]

    web_ingress = _named_resource(documents, "NetworkPolicy", "enterprise-doc-web-ingress")
    namespaces = {
        peer["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"]
        for rule in web_ingress["spec"]["ingress"]
        for peer in rule["from"]
    }
    assert namespaces == {"kube-system"}


def test_staging_deploy_workflow_can_select_the_reviewed_tiny_overlay() -> None:
    deploy = (ROOT / ".github/workflows/deploy-staging.yml").read_text(encoding="utf-8")
    assert "STAGING_DEPLOYMENT_PROFILE" in deploy
    assert "vars.STAGING_DEPLOYMENT_PROFILE" in deploy
    assert "tiny-single-node" in deploy
    assert 'OVERLAY_DIR="infra/k8s/overlays/${DEPLOYMENT_PROFILE}"' in deploy
    assert 'case "$DEPLOYMENT_PROFILE" in' in deploy
    annotation = "enterprise-doc-agent/deployment-profile"
    for profile in ("staging", "tiny-single-node"):
        documents = _render_kustomization(ROOT / "infra" / "k8s" / "overlays" / profile)
        namespace = _named_resource(documents, "Namespace", "enterprise-doc-agent-staging")
        assert namespace["metadata"]["annotations"][annotation] == profile

    guard = deploy.index("Validate deployment profile ownership")
    prerequisites = deploy.index("Apply and validate staging prerequisites")
    assert guard < prerequisites
    assert "kubectl get namespace enterprise-doc-agent-staging --ignore-not-found -o json" in deploy
    assert annotation in deploy
    assert "existing namespace is missing its deployment profile annotation" in deploy
    assert "existing namespace belongs to deployment profile" in deploy


def test_tiny_overlay_binds_exact_images_through_the_staging_parent(tmp_path: Path) -> None:
    kustomize = shutil.which("kustomize")
    assert kustomize is not None, "standalone kustomize is required to validate image binding"
    k8s = tmp_path / "k8s"
    shutil.copytree(ROOT / "infra" / "k8s", k8s)
    staging = k8s / "overlays" / "staging"
    tiny = k8s / "overlays" / "tiny-single-node"
    registry = "ghcr.io/example"
    digests = {
        "api": "sha256:" + "1" * 64,
        "worker": "sha256:" + "2" * 64,
        "consumer": "sha256:" + "3" * 64,
        "web": "sha256:" + "4" * 64,
    }
    for name, digest in digests.items():
        subprocess.run(
            [
                kustomize,
                "edit",
                "set",
                "image",
                f"enterprise-doc/{name}={registry}/enterprise-doc-{name}@{digest}",
            ],
            cwd=staging,
            check=True,
            capture_output=True,
            text=True,
        )
    rendered = subprocess.run(
        [kustomize, "build", str(tiny)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    documents = [item for item in yaml.safe_load_all(rendered.stdout) if isinstance(item, dict)]
    expected = {
        f"enterprise-doc-{name}": f"{registry}/enterprise-doc-{name}@{digest}"
        for name, digest in digests.items()
    }
    for name, image in expected.items():
        deployment = _named_resource(documents, "Deployment", name)
        assert deployment["spec"]["template"]["spec"]["containers"][0]["image"] == image
    migration = _named_resource(documents, "Job", "enterprise-doc-migrate")
    assert (
        migration["spec"]["template"]["spec"]["containers"][0]["image"]
        == expected["enterprise-doc-api"]
    )
    assert "registry.example.invalid" not in rendered.stdout

    deploy = (ROOT / ".github/workflows/deploy-staging.yml").read_text(encoding="utf-8")
    assert 'IMAGE_BINDING_OVERLAY="infra/k8s/overlays/staging"' in deploy
    assert 'cd "$IMAGE_BINDING_OVERLAY"' in deploy


def test_network_policy_and_prod_environment_are_explicit() -> None:
    policies = _documents(ROOT / "infra/k8s/base/network-policy.yaml")
    egress = next(
        item for item in policies if item["metadata"]["name"] == "enterprise-doc-runtime-egress"
    )
    ports = {port["port"] for rule in egress["spec"]["egress"] for port in rule.get("ports", [])}
    assert 443 in ports
    api_ingress = next(
        item for item in policies if item["metadata"]["name"] == "enterprise-doc-api-ingress"
    )
    assert all(
        "namespaceSelector" not in peer
        for rule in api_ingress["spec"]["ingress"]
        for peer in rule["from"]
    )
    prod_patch = (ROOT / "infra/k8s/overlays/prod/configmap-patch.yaml").read_text(encoding="utf-8")
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
    assert "EXPECTED_CONTEXT" in rollback
    assert "current-context" in rollback
    assert "STAGING_KUBE_API_SERVER" in rollback
    assert "STAGING_NAMESPACE_UID" in rollback
    assert "actual_api_server" in rollback
    assert "actual_namespace_uid" in rollback
    assert "kubectl auth can-i get deployments.apps" in rollback
    assert "kubectl auth can-i patch deployments.apps" in rollback
    assert "kubectl auth can-i watch deployments.apps" in rollback
    assert "configure_staging_manifest.py" in deploy
    assert "validate_staging_secrets.py" in deploy
    assert "sanitize_deployment_evidence.py" in deploy
    assert "build_staging_release_record.py" in deploy
    assert "--dry-run=server" in deploy
    assert "object_store_endpoint" in deploy
    assert "object_store_presign_endpoint" in deploy
    assert "VITE_OBJECT_STORE_ORIGINS" in deploy
    assert "STAGING_DATABASE_EGRESS_CIDR" in deploy
    assert "--database-egress-cidr" in deploy
    assert "enterprise-doc-registry" in deploy
    assert 'evidence_id="${GITHUB_SHA}"' in deploy
    assert "staging-sanitized-${evidence_id}" in deploy
    assert "staging-evidence-${evidence_id}.manifest.json" in deploy
    assert "staging-release-${evidence_id}.record.json" in deploy
    assert "if-no-files-found: error" in deploy
    assert "curlimages/curl@sha256:" in deploy
    assert "--labels=app.kubernetes.io/name=m5-staging-smoke" in deploy
    profile_evidence = 'printf \'%s\\n\' "$DEPLOYMENT_PROFILE" > "$raw_dir/deployment-profile.txt"'
    assert profile_evidence in deploy
    assert deploy.index(profile_evidence) < deploy.index("scripts/sanitize_deployment_evidence.py")
    rollback_script = (ROOT / "scripts/rollback_release.py").read_text(encoding="utf-8")
    assert "--to-revision" in rollback_script


def test_tagged_supply_chain_verifies_the_exact_published_digest() -> None:
    _, steps = _workflow_steps("container.yml")

    local_build = _named_step(steps, "Build image for contract and scan")
    assert "!startsWith(github.ref, 'refs/tags/')" in str(local_build["if"])

    release_build = _named_step(steps, "Push immutable release image")
    assert release_build["id"] == "push"
    assert "startsWith(github.ref, 'refs/tags/')" in str(release_build["if"])
    release_settings = release_build["with"]
    assert isinstance(release_settings, dict)
    assert release_settings["push"] is True
    assert release_settings["provenance"] == "mode=max"

    digest_expression = "steps.push.outputs.digest"
    release_scan = _named_step(steps, "Scan published image")
    assert release_scan["id"] == "release_scan"
    scan_settings = release_scan["with"]
    assert isinstance(scan_settings, dict)
    assert digest_expression in str(scan_settings["image-ref"])

    release_sbom = _named_step(steps, "Generate published SBOM")
    assert release_sbom["id"] == "release_sbom"
    sbom_settings = release_sbom["with"]
    assert isinstance(sbom_settings, dict)
    assert digest_expression in str(sbom_settings["image"])

    provenance = _named_step(steps, "Extract published BuildKit provenance")
    provenance_command = str(provenance["run"])
    assert "docker buildx imagetools inspect" in provenance_command
    assert ".Provenance.SLSA" in provenance_command
    assert "jq -e" in provenance_command
    assert 'type == "object"' in provenance_command
    assert "buildkit-provenance-${{ matrix.name }}.log" in provenance_command
    assert digest_expression in str(provenance["env"])

    signature = _named_step(steps, "Sign and attest immutable digest")
    cosign = _named_step(steps, "Install Cosign")
    assert cosign["id"] == "cosign"
    assert "steps.cosign.outcome == 'success'" in str(signature["if"])
    signature_command = str(signature["run"])
    assert "cosign sign" in signature_command
    assert "cosign attest" in signature_command
    assert "spdxjson" in signature_command
    assert "slsaprovenance" in signature_command

    verification = _named_step(steps, "Verify release attestations")
    assert "steps.sign.outcome == 'success'" in str(verification["if"])
    verification_command = str(verification["run"])
    assert "cosign verify" in verification_command
    assert "cosign verify-attestation" in verification_command
    assert "--type spdxjson" in verification_command
    assert "--type slsaprovenance" in verification_command
    assert "2> cosign-signature-verify-${{ matrix.name }}.log" in verification_command
    assert "2> cosign-sbom-attestation-verify-${{ matrix.name }}.log" in verification_command
    assert "2> cosign-provenance-verify-${{ matrix.name }}.log" in verification_command
    assert digest_expression in str(verification["env"])

    for step_name in ("Scan local image", "Scan published image"):
        trivy = _named_step(steps, step_name)
        assert trivy["uses"] == (
            "aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25"
        )
        assert trivy["with"]["version"] == "v0.70.0"

    evidence = _named_step(steps, "Upload release supply-chain evidence")
    assert "always()" in str(evidence["if"])
    evidence_settings = evidence["with"]
    assert isinstance(evidence_settings, dict)
    evidence_paths = str(evidence_settings["path"])
    for expected in (
        "image-${{ matrix.name }}.digest.txt",
        "trivy-${{ matrix.name }}.sarif",
        "sbom-${{ matrix.name }}.spdx.json",
        "buildkit-provenance-${{ matrix.name }}.json",
        "buildkit-provenance-${{ matrix.name }}.log",
        "cosign-sign-attest-${{ matrix.name }}.log",
        "cosign-signature-verify-${{ matrix.name }}.json",
        "cosign-signature-verify-${{ matrix.name }}.log",
        "cosign-sbom-attestation-verify-${{ matrix.name }}.json",
        "cosign-sbom-attestation-verify-${{ matrix.name }}.log",
        "cosign-provenance-verify-${{ matrix.name }}.json",
        "cosign-provenance-verify-${{ matrix.name }}.log",
        "release-step-outcomes-${{ matrix.name }}.json",
    ):
        assert expected in evidence_paths


def test_all_workflow_actions_are_pinned_to_immutable_commits() -> None:
    for workflow_path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        assert isinstance(workflow, dict)
        stack: list[object] = [workflow]
        uses: list[str] = []
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                for key, child in value.items():
                    if key == "uses":
                        uses.append(str(child))
                    else:
                        stack.append(child)
            elif isinstance(value, list):
                stack.extend(value)
        assert uses, workflow_path
        assert all(ACTION_SHA.fullmatch(item) for item in uses), (workflow_path, uses)


def test_release_manifest_strictly_aggregates_all_image_evidence() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "container.yml").read_text(encoding="utf-8")
    )
    jobs = workflow["jobs"]
    release = jobs["release-manifest"]
    assert release["needs"] == "images"
    steps = [step for step in release["steps"] if isinstance(step, dict)]
    download = _named_step(steps, "Download per-image evidence")
    assert download["with"]["pattern"] == "release-supply-chain-*"
    assert download["with"]["merge-multiple"] is True
    manifest = _named_step(steps, "Build strict release manifest")
    assert "scripts/build_release_manifest.py" in str(manifest["run"])
    upload = _named_step(steps, "Upload strict release manifest")
    assert upload["with"]["if-no-files-found"] == "error"


def test_release_registry_is_parameterized_but_defaults_to_ghcr() -> None:
    text = (ROOT / ".github" / "workflows" / "container.yml").read_text(encoding="utf-8")
    assert "CONTAINER_REGISTRY" in text
    assert "CONTAINER_REGISTRY_NAMESPACE" in text
    assert "vars.CONTAINER_REGISTRY" in text
    assert "vars.CONTAINER_REGISTRY_NAMESPACE" in text
    assert "secrets.CONTAINER_REGISTRY_USERNAME" in text
    assert "secrets.CONTAINER_REGISTRY_PASSWORD" in text
    assert "steps.release_ref.outputs.prefix" in text
