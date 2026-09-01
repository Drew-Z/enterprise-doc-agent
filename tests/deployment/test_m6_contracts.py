from __future__ import annotations

import hashlib
import json
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
    command: list[str]
    if kustomize is not None:
        command = [kustomize, "build", str(path)]
    else:
        kubectl = shutil.which("kubectl")
        assert kubectl is not None, "kustomize or kubectl is required to validate overlays"
        command = [kubectl, "kustomize", str(path)]
    completed = subprocess.run(
        command,
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


def _deploy_workflow() -> tuple[dict[str, object], list[dict[str, object]]]:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "deploy-staging.yml").read_text(encoding="utf-8")
    )
    assert isinstance(workflow, dict)
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    deploy = jobs["deploy"]
    assert isinstance(deploy, dict)
    steps = deploy["steps"]
    assert isinstance(steps, list)
    return workflow, [step for step in steps if isinstance(step, dict)]


def test_all_runtime_images_have_non_root_dockerfiles_and_no_floating_latest_tag() -> None:
    docker_dir = ROOT / "infra" / "docker"
    for name in ("api", "worker", "consumer", "web"):
        text = (docker_dir / f"Dockerfile.{name}").read_text(encoding="utf-8")
        assert "FROM " in text
        assert "USER " in text
        assert ":latest" not in text
        assert "10001" in text or name == "web"
        assert all("@sha256:" in line for line in text.splitlines() if line.startswith("FROM "))


def test_web_runtime_prepares_nginx_pid_before_dropping_privileges() -> None:
    dockerfile = (ROOT / "infra" / "docker" / "Dockerfile.web").read_text(encoding="utf-8")
    privileged_build = dockerfile.split("USER nginx", maxsplit=1)[0]
    assert "touch /run/nginx.pid" in privileged_build
    assert re.search(r"chown [^\n]*/run/nginx\.pid", privileged_build)


def test_web_runtime_uses_origin_base_and_proxies_api_readiness() -> None:
    dockerfile = (ROOT / "infra" / "docker" / "Dockerfile.web").read_text(encoding="utf-8")
    assert "ARG VITE_API_BASE_URL\n" in dockerfile
    assert "ARG VITE_API_BASE_URL=/api" not in dockerfile

    nginx = (ROOT / "infra" / "docker" / "nginx.conf").read_text(encoding="utf-8")
    readiness = nginx.split("location = /health/ready", maxsplit=1)[1].split("}", maxsplit=1)[0]
    assert "proxy_pass http://enterprise-doc-api:8000;" in readiness

    _, steps = _workflow_steps("container.yml")
    local_build = _named_step(steps, "Build image for contract and scan")
    release_build = _named_step(steps, "Push immutable release image")
    for step in (local_build, release_build):
        build_args = str(step["with"]["build-args"])
        assert "VITE_API_BASE_URL=" in build_args
        assert "VITE_API_BASE_URL=/api" not in build_args


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
    command = migration["spec"]["template"]["spec"]["containers"][0]["command"]
    assert command[:2] == ["sh", "-ec"]
    expected_script = (
        "alembic upgrade head\n"
        "enterprise-doc-checkpointer-setup --setup\n"
        "enterprise-doc-checkpointer-setup --check\n"
    )
    assert command[2] == expected_script
    assert migration["spec"]["activeDeadlineSeconds"] == 2700

    guardrails = _documents(ROOT / "infra/k8s/bootstrap/staging-deployer-guardrails.yaml")
    job_guard = _named_resource(
        guardrails,
        "ValidatingAdmissionPolicy",
        "enterprise-doc-staging-job-guard",
    )
    migration_validation = str(job_guard["spec"]["validations"][2]["expression"])
    expected_cel_script = expected_script.replace("\n", "\\n")
    assert f"'{expected_cel_script}'" in migration_validation

    embedding_rollout = _documents(ROOT / "infra/k8s/overlays/staging/embedding-rollout-job.yaml")[
        0
    ]
    rollout_pod = embedding_rollout["spec"]["template"]["spec"]
    rollout_container = rollout_pod["containers"][0]
    assert embedding_rollout["spec"]["activeDeadlineSeconds"] == 1260
    assert embedding_rollout["spec"]["backoffLimit"] == 0
    assert rollout_pod["automountServiceAccountToken"] is False
    assert rollout_pod["serviceAccountName"] == "enterprise-doc-runtime"
    assert rollout_pod["securityContext"]["runAsNonRoot"] is True
    assert rollout_container["command"] == ["enterprise-doc-embedding-rollout"]
    assert rollout_container["args"] == [
        "--limit",
        "1000",
        "--deadline-seconds",
        "1200",
        "--poll-seconds",
        "5",
    ]
    assert rollout_container["envFrom"] == [
        {"configMapRef": {"name": "enterprise-doc-config"}},
        {"secretRef": {"name": "enterprise-doc-secrets"}},
    ]
    assert rollout_container["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }
    assert "enterprise-doc-embedding-rollout" in migration_validation
    assert "['enterprise-doc-embedding-rollout']" in migration_validation

    policy_docs = _documents(ROOT / "infra/k8s/base/network-policy.yaml")
    assert any(item["metadata"]["name"] == "enterprise-doc-default-deny" for item in policy_docs)
    assert any(item["metadata"]["name"] == "enterprise-doc-runtime-egress" for item in policy_docs)


def test_secret_example_is_not_referenced_as_a_real_secret() -> None:
    base = (ROOT / "infra/k8s/base/kustomization.yaml").read_text(encoding="utf-8")
    assert "secret.example.yaml" not in base
    secret = (ROOT / "infra/k8s/base/secret.example.yaml").read_text(encoding="utf-8")
    assert "replace-with-secret-manager-reference" in secret


def test_staging_deployer_bootstrap_cannot_read_or_mount_unreviewed_secrets() -> None:
    bootstrap = ROOT / "infra" / "k8s" / "bootstrap"
    documents = [
        document
        for name in ("staging-deployer-rbac.yaml", "staging-deployer-guardrails.yaml")
        for document in _documents(bootstrap / name)
    ]
    assert {item["kind"] for item in documents} == {
        "Namespace",
        "ServiceAccount",
        "Secret",
        "ClusterRole",
        "ClusterRoleBinding",
        "Role",
        "RoleBinding",
        "ValidatingAdmissionPolicy",
        "ValidatingAdmissionPolicyBinding",
    }

    namespace = _named_resource(documents, "Namespace", "enterprise-doc-agent-staging")
    assert namespace["metadata"]["annotations"]["enterprise-doc-agent/deployment-profile"] == (
        "single-node-4c4g"
    )
    assert (
        namespace["metadata"]["labels"]
        | {
            "pod-security.kubernetes.io/enforce": "restricted",
            "pod-security.kubernetes.io/enforce-version": "latest",
            "pod-security.kubernetes.io/audit": "restricted",
            "pod-security.kubernetes.io/audit-version": "latest",
        }
        == namespace["metadata"]["labels"]
    )
    service_account = _named_resource(
        documents,
        "ServiceAccount",
        "enterprise-doc-staging-deployer",
    )
    assert service_account["automountServiceAccountToken"] is False

    token_secret = _named_resource(
        documents,
        "Secret",
        "enterprise-doc-staging-deployer-token",
    )
    assert token_secret["type"] == "kubernetes.io/service-account-token"
    assert token_secret["metadata"]["annotations"] == {
        "kubernetes.io/service-account.name": "enterprise-doc-staging-deployer"
    }

    namespace_role = _named_resource(
        documents,
        "ClusterRole",
        "enterprise-doc-staging-namespace-manager",
    )
    namespace_rules = namespace_role["rules"]
    assert namespace_rules == [
        {
            "apiGroups": [""],
            "resources": ["namespaces"],
            "resourceNames": ["enterprise-doc-agent-staging"],
            "verbs": ["get"],
        }
    ]

    namespaced_role = _named_resource(documents, "Role", "enterprise-doc-staging-deployer")
    rules = namespaced_role["rules"]
    assert all("secrets" not in rule["resources"] for rule in rules)
    assert all(rule["apiGroups"] != ["rbac.authorization.k8s.io"] for rule in rules)
    resource_verbs = {
        (api_group, resource): set(rule["verbs"])
        for rule in rules
        for api_group in rule["apiGroups"]
        for resource in rule["resources"]
    }
    read_only = {"get", "list", "watch"}
    for identity in (
        ("", "configmaps"),
        ("", "persistentvolumeclaims"),
        ("", "serviceaccounts"),
        ("", "services"),
        ("", "pods"),
        ("", "pods/log"),
        ("", "events"),
        ("apps", "replicasets"),
        ("policy", "poddisruptionbudgets"),
        ("networking.k8s.io", "ingresses"),
        ("networking.k8s.io", "networkpolicies"),
    ):
        assert resource_verbs[identity] == read_only
    assert ("", "pods/attach") not in resource_verbs
    assert "create" in resource_verbs[("apps", "deployments")]
    assert "patch" in resource_verbs[("apps", "deployments")]
    assert resource_verbs[("apps", "deployments/scale")] == {"patch"}
    job_rules = [
        rule for rule in rules if rule["apiGroups"] == ["batch"] and rule["resources"] == ["jobs"]
    ]
    assert {tuple(rule.get("resourceNames", [])): set(rule["verbs"]) for rule in job_rules} == {
        (): {"get", "list", "watch", "create", "update", "patch"},
        (
            "enterprise-doc-migrate",
            "enterprise-doc-embedding-rollout",
            "m5-staging-smoke",
        ): {"delete"},
    }

    runtime_role = _named_resource(documents, "Role", "enterprise-doc-runtime")
    runtime_binding = _named_resource(documents, "RoleBinding", "enterprise-doc-runtime")
    assert runtime_role["rules"] == []
    assert runtime_binding["roleRef"]["name"] == "enterprise-doc-runtime"

    policies = [item for item in documents if item["kind"] == "ValidatingAdmissionPolicy"]
    bindings = [item for item in documents if item["kind"] == "ValidatingAdmissionPolicyBinding"]
    assert len(policies) == 4
    assert {item["metadata"]["name"] for item in bindings} == {
        item["metadata"]["name"] for item in policies
    }
    policy_text = (bootstrap / "staging-deployer-guardrails.yaml").read_text(encoding="utf-8")
    assert "system:serviceaccount:enterprise-doc-agent-staging:enterprise-doc-staging-deployer" in (
        policy_text
    )
    assert "enterprise-doc-secrets" in policy_text
    assert "enterprise-doc-registry" in policy_text
    assert "enterprise-doc-staging-deployer-token" not in policy_text
    assert "paramKind" not in policy_text
    assert "paramRef" not in policy_text
    assert "namespaceObject.metadata.annotations" in policy_text
    assert "enterprise-doc-agent/approved-api-images" in policy_text
    assert "enterprise-doc-agent/approved-worker-images" in policy_text
    assert "enterprise-doc-agent/approved-consumer-images" in policy_text
    assert "enterprise-doc-agent/approved-web-images" in policy_text
    assert "enterprise-doc-agent/approved-prometheus-images" in policy_text
    assert "enterprise-doc-agent/approved-model-fallback-provider" in policy_text
    assert "enterprise-doc-agent/approved-model-fallback-base-url" in policy_text
    assert "enterprise-doc-agent/approved-model-fallback-name" in policy_text
    assert "enterprise-doc-agent/approved-model-fallback-version" in policy_text
    assert "enterprise-doc-agent/approved-model-fallback-timeout-seconds" in policy_text
    assert "enterprise-doc-agent/approved-model-fallback-secret-key" in policy_text
    assert "automountServiceAccountToken" in policy_text
    assert "serviceAccountToken" in policy_text
    assert "pod-security.kubernetes.io/enforce" in policy_text

    deployment_guard = _named_resource(
        policies,
        "ValidatingAdmissionPolicy",
        "enterprise-doc-staging-deployment-guard",
    )
    entrypoint_validation = next(
        validation
        for validation in deployment_guard["spec"]["validations"]
        if validation["message"]
        == "Deployment entrypoints and arguments must match the reviewed runtime."
    )
    entrypoint_expression = str(entrypoint_validation["expression"])
    assert "enterprise-doc-agent/deployment-profile" in entrypoint_expression
    assert "'single-node-4c8g'" in entrypoint_expression
    assert "['tiny-single-node', 'staging']" in entrypoint_expression
    assert "'128mb'" in entrypoint_expression
    assert "'48mb'" in entrypoint_expression

    prerequisite_guard = _named_resource(
        policies,
        "ValidatingAdmissionPolicy",
        "enterprise-doc-staging-prerequisite-guard",
    )
    guarded_resources = {
        resource
        for rule in prerequisite_guard["spec"]["matchConstraints"]["resourceRules"]
        for resource in rule["resources"]
    }
    assert guarded_resources == {
        "configmaps",
        "persistentvolumeclaims",
        "serviceaccounts",
        "services",
        "poddisruptionbudgets",
        "ingresses",
        "networkpolicies",
    }
    assert prerequisite_guard["spec"]["validations"] == [
        {
            "expression": "false",
            "message": "Staging prerequisites are administrator-owned.",
            "reason": "Forbidden",
        }
    ]

    deploy = (ROOT / ".github/workflows/deploy-staging.yml").read_text(encoding="utf-8")
    assert not re.search(r"kubectl(?!\s+auth\s+can-i)[^\n]*\bget\s+secrets?\b", deploy)
    assert "staging-secrets.json" not in deploy


def test_database_url_has_one_secret_backed_source() -> None:
    config = (ROOT / "infra/k8s/base/configmap.yaml").read_text(encoding="utf-8")
    secret = (ROOT / "infra/k8s/base/secret.example.yaml").read_text(encoding="utf-8")
    assert "DATABASE__URL" not in config
    assert "DATABASE__URL" in secret


def test_fallback_model_api_key_has_one_secret_backed_source() -> None:
    config = (ROOT / "infra/k8s/base/configmap.yaml").read_text(encoding="utf-8")
    secret = (ROOT / "infra/k8s/base/secret.example.yaml").read_text(encoding="utf-8")
    assert "MODEL__FALLBACK_API_KEY" not in config
    assert "MODEL__FALLBACK_API_KEY" in secret
    validator = (ROOT / "scripts/validate_staging_secrets.py").read_text(encoding="utf-8")
    runbook = (ROOT / "docs/ops/tiny-staging-runbook.md").read_text(encoding="utf-8")
    assert "--require-model-fallback-api-key" in validator
    assert "--require-model-fallback-api-key" in runbook


def test_staging_overlay_defines_https_ingress_and_private_registry_contract() -> None:
    overlay = ROOT / "infra" / "k8s" / "overlays" / "staging"
    kustomization = (overlay / "kustomization.yaml").read_text(encoding="utf-8")
    assert "ingress.yaml" in kustomization
    assert "web-ingress-policy.yaml" in kustomization
    assert "smoke-api-ingress-policy.yaml" in kustomization
    assert "smoke-api-egress-policy.yaml" in kustomization
    assert "configmap-patch.yaml" in kustomization
    assert "image-pull-secret-patch.yaml" in kustomization

    ingress = _documents(overlay / "ingress.yaml")[0]
    assert ingress["kind"] == "Ingress"
    assert ingress["spec"]["tls"][0]["secretName"] == "enterprise-doc-staging-tls"
    assert ingress["spec"]["rules"][0]["host"] == "staging.example.invalid"

    pull_patches = _documents(overlay / "image-pull-secret-patch.yaml")
    assert {(item["kind"], item["metadata"]["name"]) for item in pull_patches} == {
        ("Job", "enterprise-doc-migrate"),
        ("Job", "enterprise-doc-embedding-rollout"),
        ("Deployment", "enterprise-doc-api"),
        ("Deployment", "enterprise-doc-worker"),
        ("Deployment", "enterprise-doc-consumer"),
        ("Deployment", "enterprise-doc-web"),
    }
    assert {item["metadata"]["name"] for item in pull_patches} == {
        "enterprise-doc-migrate",
        "enterprise-doc-embedding-rollout",
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

    smoke_egress = _named_resource(
        documents,
        "NetworkPolicy",
        "enterprise-doc-staging-smoke-api-egress",
    )
    assert smoke_egress["spec"] == {
        "podSelector": {
            "matchLabels": {"app.kubernetes.io/name": "m5-staging-smoke"},
        },
        "policyTypes": ["Egress"],
        "egress": [
            {
                "to": [
                    {
                        "podSelector": {
                            "matchLabels": {
                                "app.kubernetes.io/name": "enterprise-doc-api",
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
    config = _named_resource(documents, "ConfigMap", "enterprise-doc-config")
    assert config["data"]["DATABASE__POOL_SIZE"] == "3"
    assert config["data"]["DATABASE__MAX_OVERFLOW"] == "2"
    assert config["data"]["DATABASE__POOL_TIMEOUT_SECONDS"] == "10"
    assert config["data"]["DATABASE__POOL_RECYCLE_SECONDS"] == "600"
    database_processes_during_migration = 2 + 1 + 1 + 1
    assert (
        database_processes_during_migration
        * (
            int(config["data"]["DATABASE__POOL_SIZE"])
            + int(config["data"]["DATABASE__MAX_OVERFLOW"])
        )
        <= 25
    )
    assert "nginx.ingress.kubernetes.io/force-ssl-redirect" not in ingress["metadata"].get(
        "annotations", {}
    )

    config = _named_resource(documents, "ConfigMap", "enterprise-doc-config")
    assert config["data"]["REDIS__URL"] == "redis://enterprise-doc-redis:6379/0"
    assert config["data"]["DATABASE__CONNECT_TIMEOUT_SECONDS"] == "15"
    assert config["data"]["OBJECT_STORE__CONNECT_TIMEOUT_SECONDS"] == "15"
    assert config["data"]["AGENT__CHECKPOINT_TIMEOUT_SECONDS"] == "60"
    assert config["data"]["AGENT__EXECUTION_MAX_ATTEMPTS"] == "5"
    assert config["data"]["MCP__REQUEST_TIMEOUT_SECONDS"] == "90"
    assert config["data"]["EMBEDDING__INGESTION_MAX_ATTEMPTS"] == "5"
    assert str(config["data"]["OBJECT_STORE__ENDPOINT"]).startswith("https://")
    assert config["data"]["MODEL__PROVIDER"] == "openai_compatible"
    assert str(config["data"]["MODEL__BASE_URL"]).endswith("/v1")
    assert config["data"]["MODEL__MODEL_NAME"] == "replace-with-reviewed-model"
    assert config["data"]["EMBEDDING__PROVIDER"] == "openai_compatible"
    assert str(config["data"]["EMBEDDING__BASE_URL"]).endswith("/v1")
    assert config["data"]["EMBEDDING__MODEL_NAME"] == ("replace-with-reviewed-embedding-model")
    assert config["data"]["EMBEDDING__DIMENSION"] == "1024"
    assert config["data"]["EMBEDDING__VERSION"] == "2"
    assert config["data"]["EMBEDDING__SEND_DIMENSIONS"] == "true"
    assert config["data"]["EMBEDDING__QUERY_INSTRUCTION"].startswith("Given a user question")
    assert config["data"]["RETRIEVAL__REQUIRE_VECTOR_EVIDENCE"] == "true"

    api = _named_resource(documents, "Deployment", "enterprise-doc-api")
    api_container = api["spec"]["template"]["spec"]["containers"][0]
    assert api_container["readinessProbe"]["timeoutSeconds"] == 20
    assert api_container["readinessProbe"]["periodSeconds"] == 20

    worker = _named_resource(documents, "Deployment", "enterprise-doc-worker")
    worker_container = worker["spec"]["template"]["spec"]["containers"][0]
    assert worker_container["startupProbe"]["failureThreshold"] == 60
    assert worker_container["readinessProbe"]["timeoutSeconds"] == 70
    assert worker_container["readinessProbe"]["periodSeconds"] == 30
    assert worker_container["resources"]["limits"] == {"cpu": "400m", "memory": "256Mi"}
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
                    "enterprise-doc-embedding-rollout",
                ],
            },
        ],
    }
    assert external_db["spec"]["egress"] == [
        {
            "to": [{"ipBlock": {"cidr": "192.0.2.1/32"}}],
            "ports": [
                {"protocol": "TCP", "port": 5432},
                {"protocol": "TCP", "port": 6543},
            ],
        },
    ]

    web_ingress = _named_resource(documents, "NetworkPolicy", "enterprise-doc-web-ingress")
    namespaces = {
        peer["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"]
        for rule in web_ingress["spec"]["ingress"]
        for peer in rule["from"]
    }
    assert namespaces == {"kube-system"}


def test_single_node_4c8g_overlay_renders_reviewed_capacity_shape() -> None:
    overlay = ROOT / "infra" / "k8s" / "overlays" / "single-node-4c8g"
    documents = _render_kustomization(overlay)
    deployments = {
        item["metadata"]["name"]: item for item in documents if item.get("kind") == "Deployment"
    }
    assert set(deployments) == {
        "enterprise-doc-api",
        "enterprise-doc-worker",
        "enterprise-doc-consumer",
        "enterprise-doc-web",
        "enterprise-doc-redis",
        "enterprise-doc-prometheus",
    }
    assert {name: item["spec"]["replicas"] for name, item in deployments.items()} == {
        "enterprise-doc-api": 2,
        "enterprise-doc-worker": 1,
        "enterprise-doc-consumer": 1,
        "enterprise-doc-web": 2,
        "enterprise-doc-redis": 1,
        "enterprise-doc-prometheus": 1,
    }
    assert not [item for item in documents if item.get("kind") == "PodDisruptionBudget"]

    expected_resources = {
        "enterprise-doc-api": (
            {"cpu": "250m", "memory": "384Mi"},
            {"cpu": "750m", "memory": "768Mi"},
        ),
        "enterprise-doc-worker": (
            {"cpu": "300m", "memory": "512Mi"},
            {"cpu": "1000m", "memory": "1Gi"},
        ),
        "enterprise-doc-consumer": (
            {"cpu": "250m", "memory": "384Mi"},
            {"cpu": "750m", "memory": "768Mi"},
        ),
        "enterprise-doc-web": (
            {"cpu": "50m", "memory": "64Mi"},
            {"cpu": "250m", "memory": "256Mi"},
        ),
        "enterprise-doc-redis": (
            {"cpu": "100m", "memory": "128Mi"},
            {"cpu": "250m", "memory": "256Mi"},
        ),
        "enterprise-doc-prometheus": (
            {"cpu": "100m", "memory": "256Mi"},
            {"cpu": "300m", "memory": "512Mi"},
        ),
    }
    total_request_memory = 0
    total_limit_memory = 0
    total_request_cpu = 0
    for name, deployment in deployments.items():
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        requests, limits = expected_resources[name]
        assert container["resources"] == {"requests": requests, "limits": limits}
        replicas = deployment["spec"]["replicas"]
        total_request_memory += replicas * _memory_mib(str(requests["memory"]))
        total_limit_memory += replicas * _memory_mib(str(limits["memory"]).replace("1Gi", "1024Mi"))
        total_request_cpu += replicas * _cpu_millicores(str(requests["cpu"]))
        if name == "enterprise-doc-redis":
            assert "128mb" in container["args"]
            assert "noeviction" in container["args"]
        elif name == "enterprise-doc-prometheus":
            assert deployment["spec"]["strategy"] == {"type": "Recreate"}
            pod = deployment["spec"]["template"]["spec"]
            assert pod["automountServiceAccountToken"] is False
            assert pod["securityContext"]["runAsUser"] == 65534
            assert pod["securityContext"]["fsGroup"] == 65534
            assert container["args"][-1] == "--web.listen-address=0.0.0.0:9090"
            assert {volume["name"] for volume in pod["volumes"]} == {"config", "storage"}
        else:
            assert deployment["spec"]["strategy"] == {
                "type": "RollingUpdate",
                "rollingUpdate": {"maxSurge": 0, "maxUnavailable": 1},
            }

    migration = _named_resource(documents, "Job", "enterprise-doc-migrate")
    migration_resources = migration["spec"]["template"]["spec"]["containers"][0]["resources"]
    assert migration_resources == {
        "requests": {"cpu": "150m", "memory": "256Mi"},
        "limits": {"cpu": "750m", "memory": "512Mi"},
    }
    total_request_memory += _memory_mib(migration_resources["requests"]["memory"])
    total_limit_memory += _memory_mib(migration_resources["limits"]["memory"])
    total_request_cpu += _cpu_millicores(migration_resources["requests"]["cpu"])
    assert total_request_memory <= 3072
    assert total_request_cpu <= 3000
    assert total_limit_memory <= 6144

    namespace = _named_resource(documents, "Namespace", "enterprise-doc-agent-staging")
    assert namespace["metadata"]["annotations"]["enterprise-doc-agent/deployment-profile"] == (
        "single-node-4c8g"
    )
    ingress = _named_resource(documents, "Ingress", "enterprise-doc-web")
    assert ingress["spec"]["ingressClassName"] == "traefik"
    assert not [
        item
        for item in documents
        if item.get("kind") in {"Deployment", "StatefulSet"}
        and item["metadata"]["name"] in {"postgres", "minio"}
    ]
    prometheus_service = _named_resource(documents, "Service", "enterprise-doc-prometheus")
    assert prometheus_service["spec"]["type"] == "ClusterIP"
    assert not [
        item
        for item in documents
        if item.get("kind") == "Service"
        and item["spec"].get("type") in {"NodePort", "LoadBalancer"}
    ]
    prometheus_pvc = _named_resource(
        documents, "PersistentVolumeClaim", "enterprise-doc-prometheus-data"
    )
    assert prometheus_pvc["spec"]["storageClassName"] == "local-path"
    assert prometheus_pvc["spec"]["resources"]["requests"]["storage"] == "5Gi"
    assert {
        item["metadata"]["name"] for item in documents if item.get("kind") == "NetworkPolicy"
    } >= {
        "enterprise-doc-prometheus-egress",
        "enterprise-doc-api-metrics-ingress",
        "enterprise-doc-worker-metrics-ingress",
        "enterprise-doc-consumer-metrics-ingress",
    }


def test_single_node_4c4g_overlay_matches_current_server_envelope() -> None:
    overlay = ROOT / "infra" / "k8s" / "overlays" / "single-node-4c4g"
    documents = _render_kustomization(overlay)
    deployments = {
        item["metadata"]["name"]: item for item in documents if item.get("kind") == "Deployment"
    }
    assert set(deployments) == {
        "enterprise-doc-api",
        "enterprise-doc-worker",
        "enterprise-doc-consumer",
        "enterprise-doc-web",
        "enterprise-doc-redis",
    }
    assert {name: item["spec"]["replicas"] for name, item in deployments.items()} == {
        "enterprise-doc-api": 1,
        "enterprise-doc-worker": 1,
        "enterprise-doc-consumer": 1,
        "enterprise-doc-web": 1,
        "enterprise-doc-redis": 1,
    }
    expected_resources = {
        "enterprise-doc-api": (
            {"cpu": "150m", "memory": "256Mi"},
            {"cpu": "600m", "memory": "512Mi"},
        ),
        "enterprise-doc-worker": (
            {"cpu": "150m", "memory": "256Mi"},
            {"cpu": "700m", "memory": "640Mi"},
        ),
        "enterprise-doc-consumer": (
            {"cpu": "100m", "memory": "192Mi"},
            {"cpu": "500m", "memory": "384Mi"},
        ),
        "enterprise-doc-web": (
            {"cpu": "25m", "memory": "64Mi"},
            {"cpu": "150m", "memory": "128Mi"},
        ),
        "enterprise-doc-redis": (
            {"cpu": "50m", "memory": "128Mi"},
            {"cpu": "200m", "memory": "192Mi"},
        ),
    }
    total_request_memory = 0
    total_limit_memory = 0
    for name, deployment in deployments.items():
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        requests, limits = expected_resources[name]
        assert container["resources"] == {"requests": requests, "limits": limits}
        total_request_memory += _memory_mib(requests["memory"])
        total_limit_memory += _memory_mib(limits["memory"])
        if name == "enterprise-doc-redis":
            assert "128mb" in container["args"]
        else:
            assert deployment["spec"]["strategy"] == {
                "type": "RollingUpdate",
                "rollingUpdate": {"maxSurge": 0, "maxUnavailable": 1},
            }
    migration = _named_resource(documents, "Job", "enterprise-doc-migrate")
    migration_resources = migration["spec"]["template"]["spec"]["containers"][0]["resources"]
    assert migration_resources == {
        "requests": {"cpu": "100m", "memory": "192Mi"},
        "limits": {"cpu": "500m", "memory": "384Mi"},
    }
    total_request_memory += _memory_mib(migration_resources["requests"]["memory"])
    total_limit_memory += _memory_mib(migration_resources["limits"]["memory"])
    assert total_request_memory <= 1200
    assert total_limit_memory <= 2400
    assert not [
        item
        for item in documents
        if item.get("metadata", {}).get("name") == "enterprise-doc-prometheus"
    ]
    namespace = _named_resource(documents, "Namespace", "enterprise-doc-agent-staging")
    assert namespace["metadata"]["annotations"]["enterprise-doc-agent/deployment-profile"] == (
        "single-node-4c4g"
    )
    config = _named_resource(documents, "ConfigMap", "enterprise-doc-config")
    assert config["data"]["DATABASE__POOL_SIZE"] == "1"
    assert config["data"]["DATABASE__MAX_OVERFLOW"] == "0"


def test_staging_deploy_workflow_can_select_the_reviewed_tiny_overlay() -> None:
    deploy = (ROOT / ".github/workflows/deploy-staging.yml").read_text(encoding="utf-8")
    assert "STAGING_DEPLOYMENT_PROFILE" in deploy
    assert "vars.STAGING_DEPLOYMENT_PROFILE" in deploy
    assert "tiny-single-node" in deploy
    assert 'OVERLAY_DIR="infra/k8s/overlays/${DEPLOYMENT_PROFILE}"' in deploy
    assert 'case "$DEPLOYMENT_PROFILE" in' in deploy
    annotation = "enterprise-doc-agent/deployment-profile"
    for profile in ("staging", "tiny-single-node", "single-node-4c4g", "single-node-4c8g"):
        documents = _render_kustomization(ROOT / "infra" / "k8s" / "overlays" / profile)
        namespace = _named_resource(documents, "Namespace", "enterprise-doc-agent-staging")
        assert namespace["metadata"]["annotations"][annotation] == profile

    guard = deploy.index("Validate deployment profile ownership")
    prerequisites = deploy.index("Verify administrator-owned staging prerequisites")
    assert guard < prerequisites
    assert "kubectl get namespace enterprise-doc-agent-staging --ignore-not-found -o json" in deploy
    assert annotation in deploy
    assert "existing namespace is missing its deployment profile annotation" in deploy
    assert "existing namespace belongs to deployment profile" in deploy


def test_cluster_mutating_workflows_use_the_dedicated_private_runner() -> None:
    expected = "runs-on: [self-hosted, linux, x64, enterprise-doc-staging]"
    deploy = (ROOT / ".github/workflows/deploy-staging.yml").read_text(encoding="utf-8")
    rollback = (ROOT / ".github/workflows/rollback.yml").read_text(encoding="utf-8")
    assert expected in deploy
    assert expected in rollback
    assert "runs-on: ubuntu-latest" not in deploy
    assert "runs-on: ubuntu-latest" not in rollback


def test_cluster_mutating_workflows_use_the_pre_provisioned_runner_toolchain() -> None:
    workflow_dir = ROOT / ".github" / "workflows"
    for name, job_name in (("deploy-staging.yml", "deploy"), ("rollback.yml", "rollback")):
        workflow = yaml.safe_load((workflow_dir / name).read_text(encoding="utf-8"))
        job = workflow["jobs"][job_name]
        assert job["env"]["RUNNER_PYTHON"] == "/opt/enterprise-doc-toolchain/python/bin/python"
        steps = [step for step in job["steps"] if isinstance(step, dict)]
        toolchain = _named_step(steps, "Validate pre-provisioned runner toolchain")
        command = str(toolchain["run"])
        assert 'test -x "$RUNNER_PYTHON"' in command
        assert '" = "3.12"' in command
        assert 'cryptography.__version__ == "50.0.0"' in command
        assert 'yaml.__version__ == "6.0.3"' in command
        assert "kubectl version --client" in command
        assert 'test "$(kustomize version)" = "v5.7.1"' in command
        serialized = str(workflow)
        assert "actions/setup-python" not in serialized
        assert "azure/setup-kubectl" not in serialized
        assert "setup-kustomize" not in serialized


def test_cluster_mutating_workflows_download_the_exact_source_archive() -> None:
    workflow_dir = ROOT / ".github" / "workflows"
    for name, job_name in (("deploy-staging.yml", "deploy"), ("rollback.yml", "rollback")):
        workflow = yaml.safe_load((workflow_dir / name).read_text(encoding="utf-8"))
        job = workflow["jobs"][job_name]
        assert job["defaults"]["run"]["working-directory"] == "repository"
        steps = [step for step in job["steps"] if isinstance(step, dict)]
        download = _named_step(steps, "Download exact source archive")
        assert download["working-directory"] == "${{ github.workspace }}"
        assert download["env"]["GH_TOKEN"] == "${{ github.token }}"
        command = str(download["run"])
        assert "tarball/${GITHUB_SHA}" in command
        assert "Authorization: Bearer %s" in command
        assert 'chmod 600 "$curl_config"' in command
        assert "--proto '=https' --proto-redir '=https'" in command
        assert "--connect-timeout 15 --max-time 240" in command
        assert "--retry 4 --retry-delay 3 --retry-all-errors" in command
        assert 'case "$repo_dir" in' in command
        assert 'rm -rf -- "$repo_dir" "$staging_dir"' in command
        assert "actions/checkout" not in str(workflow)


def test_staging_model_routing_uses_environment_variables_within_dispatch_limit() -> None:
    workflow, steps = _deploy_workflow()
    trigger = workflow.get("on", workflow.get(True))
    assert isinstance(trigger, dict)
    dispatch = trigger["workflow_dispatch"]
    assert isinstance(dispatch, dict)
    inputs = dispatch["inputs"]
    assert isinstance(inputs, dict)
    assert len(inputs) == 10
    assert "object_store_checksum_mode" not in inputs
    assert not {"model_provider", "model_base_url", "model_name"} & inputs.keys()

    expected_env = {
        "MODEL_PROVIDER": "openai_compatible",
        "MODEL_BASE_URL": "${{ vars.STAGING_MODEL_BASE_URL }}",
        "MODEL_NAME": "${{ vars.STAGING_MODEL_NAME }}",
        "EMBEDDING_BASE_URL": "${{ vars.STAGING_EMBEDDING_BASE_URL }}",
        "EMBEDDING_MODEL_NAME": "${{ vars.STAGING_EMBEDDING_MODEL_NAME }}",
        "EMBEDDING_VERSION": "${{ vars.STAGING_EMBEDDING_VERSION || '2' }}",
    }
    for step_name in (
        "Render and validate staging manifests",
        "Collect sanitized release evidence",
    ):
        step = _named_step(steps, step_name)
        assert {key: step["env"][key] for key in expected_env} == expected_env
        command = str(step["run"])
        assert '--model-provider "$MODEL_PROVIDER"' in command
        assert '--model-base-url "$MODEL_BASE_URL"' in command
        assert '--model-name "$MODEL_NAME"' in command
        assert '--embedding-base-url "$EMBEDDING_BASE_URL"' in command
        assert '--embedding-model-name "$EMBEDDING_MODEL_NAME"' in command
        assert '--embedding-version "$EMBEDDING_VERSION"' in command

    render = _named_step(steps, "Render and validate staging manifests")
    assert render["env"]["MODEL_FALLBACK_BASE_URL"] == (
        "${{ vars.STAGING_MODEL_FALLBACK_BASE_URL }}"
    )
    assert render["env"]["MODEL_FALLBACK_NAME"] == ("${{ vars.STAGING_MODEL_FALLBACK_NAME }}")
    assert render["env"]["MODEL_FALLBACK_VERSION"] == ("${{ vars.STAGING_MODEL_FALLBACK_VERSION }}")
    assert render["env"]["MODEL_FALLBACK_TIMEOUT_SECONDS"] == (
        "${{ vars.STAGING_MODEL_FALLBACK_TIMEOUT_SECONDS || '60' }}"
    )
    assert render["env"]["OBJECT_STORE_CHECKSUM_MODE"] == (
        "${{ vars.STAGING_OBJECT_STORE_CHECKSUM_MODE }}"
    )
    render_command = str(render["run"])
    assert 'test -n "$MODEL_BASE_URL"' in render_command
    assert 'test -n "$MODEL_NAME"' in render_command
    assert 'test -n "$EMBEDDING_BASE_URL"' in render_command
    assert 'test -n "$EMBEDDING_MODEL_NAME"' in render_command
    assert 'test -n "$OBJECT_STORE_CHECKSUM_MODE"' in render_command
    assert "fallback_args=()" in render_command
    assert '--fallback-model-base-url "$MODEL_FALLBACK_BASE_URL"' in render_command
    assert '--fallback-model-name "$MODEL_FALLBACK_NAME"' in render_command
    assert '--fallback-model-version "$MODEL_FALLBACK_VERSION"' in render_command
    assert '--fallback-model-timeout-seconds "$MODEL_FALLBACK_TIMEOUT_SECONDS"' in render_command
    assert "yaml.safe_load_all" in render_command
    assert '("Namespace", "enterprise-doc-agent-staging")' in render_command
    assert '("Job", "enterprise-doc-migrate")' in render_command
    assert "grep -A4 '^kind: Namespace$'" not in render_command

    collect = _named_step(steps, "Collect sanitized release evidence")
    for key, value in {
        "MODEL_FALLBACK_BASE_URL": "${{ vars.STAGING_MODEL_FALLBACK_BASE_URL }}",
        "MODEL_FALLBACK_NAME": "${{ vars.STAGING_MODEL_FALLBACK_NAME }}",
        "MODEL_FALLBACK_VERSION": "${{ vars.STAGING_MODEL_FALLBACK_VERSION }}",
        "MODEL_FALLBACK_TIMEOUT_SECONDS": (
            "${{ vars.STAGING_MODEL_FALLBACK_TIMEOUT_SECONDS || '60' }}"
        ),
    }.items():
        assert collect["env"][key] == value
    collect_command = str(collect["run"])
    assert "fallback_record_args=()" in collect_command
    assert '--fallback-model-base-url "$MODEL_FALLBACK_BASE_URL"' in collect_command
    assert '--fallback-model-name "$MODEL_FALLBACK_NAME"' in collect_command
    assert '--fallback-model-version "$MODEL_FALLBACK_VERSION"' in collect_command
    assert '--fallback-model-timeout-seconds "$MODEL_FALLBACK_TIMEOUT_SECONDS"' in collect_command


def test_tiny_staging_runbook_keeps_r2_presign_on_the_s3_api_surface() -> None:
    runbook = (ROOT / "docs/ops/tiny-staging-runbook.md").read_text(encoding="utf-8")
    assert "r2.cloudflarestorage.com" in runbook
    assert "use the account S3 endpoint for both" in runbook
    assert "public object-access surface" in runbook
    assert "not a substitute for the S3" in runbook
    assert "OBJECT_STORE__MULTIPART_CHECKSUM_MODE=readback_sha256" in runbook
    assert "HTTP 501" in runbook
    assert "pushd infra/k8s/overlays/staging" in runbook
    assert "Do not dispatch with the `v0.1.1` Web digest" in runbook


def test_tiny_staging_runbook_requires_isolated_restore_and_coherent_rollback() -> None:
    runbook = (ROOT / "docs/ops/tiny-staging-runbook.md").read_text(encoding="utf-8")

    assert "--expected-database" in runbook
    assert "--source-database" in runbook
    assert "enterprise_doc_restore_" in runbook
    assert "does not restore or" in runbook
    assert "validate R2 objects" in runbook
    assert "same passed release record" in runbook
    assert "list replicasets.apps" in runbook
    assert "immediately reapply the generated administrator" in runbook
    assert "20260805-staging-bidirectional-rollback.json" in runbook
    assert "verify_r2_bucket_lock.py" in runbook
    assert "Workers R2 Storage Write" in runbook
    assert "fresh isolated database" in runbook
    assert "20260806-staging-r2-recovery.json" in runbook
    assert "20260806-production-rpo-rto-drill-failed.json" in runbook
    assert "1981.0923509 seconds" in runbook
    assert "20260806-production-rpo-rto-drill-passed.json" in runbook
    assert "153.571069 seconds" in runbook
    assert "166.397916 seconds" in runbook


def test_database_r2_recovery_closes_cross_system_subgate_but_keeps_rpo_rto_open() -> None:
    evidence_path = "evidence/m6/20260805-staging-bidirectional-rollback.json"
    evidence = json.loads((ROOT / evidence_path).read_text(encoding="utf-8"))
    database_evidence_path = "evidence/m6/20260805-staging-postgres-restore.json"
    database_evidence = json.loads((ROOT / database_evidence_path).read_text(encoding="utf-8"))
    release_preflight_evidence_path = "evidence/m6/20260806-v0.1.19-staging-r2-preflight.json"
    release_preflight_evidence = json.loads(
        (ROOT / release_preflight_evidence_path).read_text(encoding="utf-8")
    )
    r2_evidence_path = "evidence/m6/20260806-staging-r2-recovery.json"
    r2_evidence = json.loads((ROOT / r2_evidence_path).read_text(encoding="utf-8"))
    application_evidence_path = "evidence/m6/20260806-isolated-application-recovery.json"
    application_evidence = json.loads(
        (ROOT / application_evidence_path).read_text(encoding="utf-8")
    )
    rpo_preflight_evidence_path = "evidence/m6/20260806-production-rpo-rto-preflight.json"
    rpo_preflight_evidence = json.loads(
        (ROOT / rpo_preflight_evidence_path).read_text(encoding="utf-8")
    )
    failed_rpo_drill_evidence_path = "evidence/m6/20260806-production-rpo-rto-drill-failed.json"
    failed_rpo_drill_evidence = json.loads(
        (ROOT / failed_rpo_drill_evidence_path).read_text(encoding="utf-8")
    )
    rpo_drill_evidence_path = "evidence/m6/20260806-production-rpo-rto-drill-passed.json"
    rpo_drill_evidence = json.loads((ROOT / rpo_drill_evidence_path).read_text(encoding="utf-8"))
    gate = json.loads(
        (ROOT / "evidence/gates/m6-backup-restore-rollback.json").read_text(encoding="utf-8")
    )
    index = json.loads((ROOT / "evidence/index.json").read_text(encoding="utf-8"))

    assert evidence["status"] == "passed_with_restore_gate_open"
    assert all(item["rollback_mutation_count"] == 0 for item in evidence["safe_preflight_failures"])
    legs = {item["name"]: item for item in evidence["legs"]}
    assert set(legs) == {"historical_release", "original_release"}
    for leg in legs.values():
        assert leg["authenticated_smoke"]["status"] == "passed"
        assert len(set(leg["requested_revisions"].values())) == 1
        assert len(leg["images"]) == 4
    assert gate["state"] == "open"
    assert gate["status"] == "blocked_external"
    assert gate["completed_evidence"] == [
        evidence_path,
        database_evidence_path,
        release_preflight_evidence_path,
        r2_evidence_path,
        application_evidence_path,
        rpo_drill_evidence_path,
    ]
    assert "measured RPO/RTO objectives passed" in gate["blocking_reason"]
    assert "fault domain" in gate["blocking_reason"]
    assert "independent reviewer" in gate["blocking_reason"]
    assert gate["planning_evidence"] == rpo_preflight_evidence_path
    assert gate["previous_failed_execution_evidence"] == failed_rpo_drill_evidence_path
    assert gate["latest_execution_evidence"] == rpo_drill_evidence_path
    assert database_evidence["status"] == "passed_database_subgate"
    assert database_evidence["restore"]["relations_before"] == 0
    assert database_evidence["restore"]["relations_after"] == 25
    assert database_evidence["data_validation"]["exact_table_counts_match"] is True
    assert database_evidence["application_readiness"]["status"] == "passed"
    for artifact in database_evidence["artifacts"]:
        artifact_path = ROOT / artifact["path"]
        assert artifact_path.is_file()
        assert hashlib.sha256(artifact_path.read_bytes()).hexdigest() == artifact["sha256"]
    assert release_preflight_evidence["status"] == "passed_with_recovery_gate_open"
    assert release_preflight_evidence["release"]["supply_chain_run"]["status"] == "passed"
    assert release_preflight_evidence["staging_rollout"]["status"] == "passed"
    assert set(release_preflight_evidence["staging_rollout"]["outcomes"].values()) == {"success"}
    assert (
        release_preflight_evidence["staging_rollout"]["authenticated_smoke"]["status"] == "passed"
    )
    assert release_preflight_evidence["r2_snapshot_preflight"]["object_count"] == 27
    assert release_preflight_evidence["r2_snapshot_preflight"]["remote_mutations"] == 0
    assert release_preflight_evidence["recovery_gate"]["bucket_lock_configured"] is False
    assert release_preflight_evidence["recovery_gate"]["confirmed_snapshot_executed"] is False
    assert r2_evidence["status"] == "passed_r2_cross_system_subgate"
    assert r2_evidence["bucket_lock"]["verified_before_snapshot"] is True
    assert r2_evidence["database_recovery"]["relations_before"] == 0
    assert r2_evidence["database_recovery"]["relations_after"] == 25
    assert r2_evidence["object_snapshot"]["object_count"] == 27
    assert r2_evidence["object_snapshot"]["copied_count"] == 27
    assert r2_evidence["object_restore"]["object_count"] == 27
    assert r2_evidence["object_restore"]["copied_count"] == 27
    validation = r2_evidence["cross_system_validation"]
    assert validation["status"] == "passed"
    assert validation["database_reference_count"] == 27
    assert validation["manifest_object_count"] == 27
    assert validation["snapshot_object_count"] == 27
    assert validation["restored_object_count"] == 27
    assert validation["database_manifest_bidirectional_match"] is True
    assert validation["manifest_restore_bidirectional_match"] is True
    assert validation["database_restore_bidirectional_match"] is True
    assert r2_evidence["secret_handling"] == {
        "cloudflare_token_in_argv": False,
        "database_urls_in_argv": False,
        "database_urls_persisted": False,
        "private_manifest_in_git": False,
        "private_object_keys_in_public_evidence": False,
        "private_records_retained_off_repository": True,
    }
    assert any("application" in item.lower() for item in r2_evidence["limitations"])
    for artifact in r2_evidence["artifacts"]:
        artifact_path = ROOT / artifact["path"]
        assert artifact_path.is_file()
        body = artifact_path.read_bytes()
        assert hashlib.sha256(body).hexdigest() == artifact["sha256"]
        rendered = body.decode("utf-8").lower()
        assert "database__url" not in rendered
        assert "secret_access_key" not in rendered
        assert "cloudflare_api_token" not in rendered
    assert application_evidence["status"] == "passed_isolated_application_recovery_smoke"
    assert application_evidence["deployment"]["workloads_ready"] is True
    assert application_evidence["recovery_target"]["public_ingress"] is False
    assert application_evidence["smoke"]["status"] == "passed"
    assert application_evidence["smoke"]["artifact_sha256_verified"] is True
    assert application_evidence["smoke"]["citation_verified_against_uploaded_version"] is True
    assert set(application_evidence["smoke"]["steps"]) == {
        "upload_session_created",
        "object_uploaded",
        "upload_completed",
        "document_ready",
        "agent_run_created",
        "agent_run_succeeded",
        "answer_artifact_listed",
        "answer_artifact_downloaded",
        "answer_artifact_sha256_verified",
        "answer_citation_verified",
    }
    assert application_evidence["secret_handling"]["tokens_in_argv"] is False
    rendered_application_evidence = json.dumps(application_evidence).lower()
    assert "database__url" not in rendered_application_evidence
    assert "secret_access_key" not in rendered_application_evidence
    assert "cloudflare_api_token" not in rendered_application_evidence
    assert rpo_preflight_evidence["status"] == "blocked_external"
    assert rpo_preflight_evidence["proposed_objectives"] == {
        "rpo_seconds": 300,
        "rto_seconds": 1800,
        "status": "pending_service_owner_approval",
        "approval_must_precede_execution": True,
    }
    assert "service-owner-approved RPO/RTO" in rpo_preflight_evidence["blocking_reason"]
    assert all(
        artifact_path.is_file()
        and hashlib.sha256(artifact_path.read_bytes()).hexdigest() == artifact["sha256"]
        for artifact in rpo_preflight_evidence["artifacts"]
        for artifact_path in [ROOT / artifact["path"]]
    )
    assert failed_rpo_drill_evidence["status"] == "failed"
    assert failed_rpo_drill_evidence["measurements"]["rpo_seconds"] == 212.2147351
    assert failed_rpo_drill_evidence["measurements"]["rto_seconds"] == 1981.0923509
    assert failed_rpo_drill_evidence["measurements"]["rpo_slo_status"] == "passed"
    assert failed_rpo_drill_evidence["measurements"]["rto_slo_status"] == "failed"
    assert failed_rpo_drill_evidence["cross_store_validation"]["second_remap"] == {
        "initial_state": "restored",
        "final_state": "restored",
        "updated_reference_count": 0,
    }
    assert rpo_drill_evidence["status"] == "passed_rpo_rto_subgate"
    assert rpo_drill_evidence["commit_sha"] == "2ba0488572994698723aee3e67a0d14f5c304413"
    assert rpo_drill_evidence["quality_run"]["run_id"] == "31115622365"
    assert rpo_drill_evidence["measurements"]["rpo_seconds"] == 153.571069
    assert rpo_drill_evidence["measurements"]["rto_seconds"] == 166.397916
    assert rpo_drill_evidence["measurements"]["rpo_slo_status"] == "passed"
    assert rpo_drill_evidence["measurements"]["rto_slo_status"] == "passed"
    assert rpo_drill_evidence["subgate_result"] == {
        "approved_objectives_met": True,
        "overall_recovery_gate_closed": False,
        "overall_gate_status": "blocked_external",
    }
    assert rpo_drill_evidence["review"] == {
        "production_like": False,
        "independent_reviewer": None,
        "sign_off_status": "pending",
    }
    assert rpo_drill_evidence["cross_store_validation"]["confirmed_remap"] == {
        "initial_state": "source",
        "final_state": "restored",
        "updated_reference_count": 47,
    }
    assert rpo_drill_evidence["cross_store_validation"]["remap_idempotency"] == {
        "status": "passed_on_separate_clean_target",
        "evidence": failed_rpo_drill_evidence_path,
        "current_drill_claim": "one confirmed source-to-restored remap only",
    }
    assert rpo_drill_evidence["recovery_scope"]["live_environment_mutated"] is False
    assert rpo_drill_evidence["recovery_scope"]["fault_domain_isolation_verified"] is False
    for artifact in rpo_drill_evidence["artifacts"]:
        artifact_path = ROOT / artifact["path"]
        assert artifact_path.is_file()
        assert hashlib.sha256(artifact_path.read_bytes()).hexdigest() == artifact["sha256"]
    rendered_rpo_drill = json.dumps(rpo_drill_evidence).lower()
    assert "database__url" not in rendered_rpo_drill
    assert "secret_access_key" not in rendered_rpo_drill
    assert "cloudflare_api_token" not in rendered_rpo_drill
    m6 = next(item for item in index["evidence"] if item["milestone"] == "M6")
    assert m6["rollback_drill"] == evidence_path
    assert m6["postgres_restore_drill"] == database_evidence_path
    assert m6["latest_staging_release"] == release_preflight_evidence_path
    assert m6["r2_snapshot_preflight"] == release_preflight_evidence_path
    assert m6["r2_recovery_drill"] == r2_evidence_path
    assert m6["isolated_application_recovery_smoke"] == application_evidence_path
    assert m6["production_rpo_rto_preflight"] == rpo_preflight_evidence_path
    assert m6["production_rpo_rto_failed_baseline"] == failed_rpo_drill_evidence_path
    assert m6["production_rpo_rto_drill"] == rpo_drill_evidence_path


def test_signed_release_and_authenticated_staging_evidence_close_delivery_gates() -> None:
    evidence_path = "evidence/m6/20260806-v0.1.19-staging-r2-preflight.json"
    evidence = json.loads((ROOT / evidence_path).read_text(encoding="utf-8"))

    for gate_name in (
        "m6-registry-signed-images.json",
        "m6-cluster-staging-rollout.json",
    ):
        gate = json.loads((ROOT / "evidence/gates" / gate_name).read_text(encoding="utf-8"))
        assert gate["state"] == "closed"
        assert gate["status"] == "passed"
        assert gate["blocking_reason"] is None
        assert gate["completed_evidence"] == [evidence_path]

    images = evidence["release"]["images"]
    assert set(images) == {"api", "worker", "consumer", "web"}
    assert all("@sha256:" in image for image in images.values())
    assert evidence["release"]["quality_run"]["status"] == "passed"
    assert evidence["release"]["supply_chain_run"]["status"] == "passed"
    assert evidence["staging_rollout"]["run_attempt"] == 3
    assert evidence["staging_rollout"]["status"] == "passed"


def test_tiny_staging_runbook_requires_private_control_plane_and_scoped_runner() -> None:
    runbook = (ROOT / "docs/ops/tiny-staging-runbook.md").read_text(encoding="utf-8")
    for private_port in ("6443/tcp", "10250/tcp", "8472/udp"):
        assert private_port in runbook
    assert "out-of-band console" in runbook
    assert "--labels enterprise-doc-staging" in runbook
    assert "repository-scoped" in runbook
    assert "infra/k8s/bootstrap/staging-deployer-rbac.yaml" in runbook
    assert "STAGING_KUBE_API_SERVER=https://127.0.0.1:6443" in runbook
    assert "cannot read its own long-lived token Secret" in runbook
    assert "STAGING_MODEL_BASE_URL=https://" in runbook
    assert "STAGING_MODEL_NAME=<exact-model-id>" in runbook
    assert "MODEL__API_KEY" in runbook
    assert "OpenAI-compatible `/v1` endpoint" in runbook


def test_tiny_overlay_binds_exact_images_through_the_staging_parent(tmp_path: Path) -> None:
    kustomize = shutil.which("kustomize")
    if kustomize is None:
        kustomize = shutil.which("kubectl")
        assert kustomize is not None, "kustomize or kubectl is required to validate image binding"
        kustomize_command = [kustomize, "kustomize"]
        edit_supported = False
    else:
        kustomize_command = [kustomize]
        edit_supported = True
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
    if edit_supported:
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
    else:
        kustomization_path = staging / "kustomization.yaml"
        kustomization = yaml.safe_load(kustomization_path.read_text(encoding="utf-8"))
        for image in kustomization["images"]:
            name = image["name"].removeprefix("enterprise-doc/")
            image["newName"] = f"{registry}/enterprise-doc-{name}"
            image["digest"] = digests[name]
        kustomization_path.write_text(
            yaml.safe_dump(kustomization, sort_keys=False),
            encoding="utf-8",
        )
    render_command = (
        [*kustomize_command, "build", str(tiny)]
        if edit_supported
        else [*kustomize_command, str(tiny)]
    )
    rendered = subprocess.run(
        render_command,
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
    assert "staging-embedding-rollout.yaml" in deploy
    assert "staging-workloads.yaml" in deploy
    assert deploy.index('test "$migration_result" != "complete"') < deploy.index(
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
    assert "kubectl auth can-i list replicasets.apps" in rollback
    assert "STAGING_KUBE_API_SERVER" in deploy
    assert "STAGING_NAMESPACE_UID" in deploy
    assert "actual_api_server" in deploy
    assert "actual_namespace_uid" in deploy
    assert "configure_staging_manifest.py" in deploy
    assert "sanitize_deployment_evidence.py" in deploy
    assert "build_staging_release_record.py" in deploy
    assert "--dry-run=server" in deploy
    assert "object_store_endpoint" in deploy
    assert "object_store_presign_endpoint" in deploy
    assert "VITE_OBJECT_STORE_ORIGINS" in deploy
    assert "STAGING_DATABASE_EGRESS_CIDRS" in deploy
    assert "STAGING_DATABASE_EGRESS_CIDR" in deploy
    assert "--database-egress-cidr" in deploy
    assert "enterprise-doc-registry" in (
        (ROOT / "infra/k8s/overlays/staging/image-pull-secret-patch.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert 'evidence_id="${GITHUB_SHA}"' in deploy
    assert "staging-sanitized-${evidence_id}" in deploy
    assert "staging-evidence-${evidence_id}.manifest.json" in deploy
    assert "staging-release-${evidence_id}.record.json" in deploy
    assert "if-no-files-found: error" in deploy
    assert "infra/k8s/smoke/readiness-job.yaml" in deploy
    assert "kubectl run" not in deploy
    assert "kubectl exec" not in deploy
    assert "validate_embedding_rollout_report.py" in deploy
    assert "--embedding-rollout-report" in deploy
    smoke = (ROOT / "infra/k8s/smoke/readiness-job.yaml").read_text(encoding="utf-8")
    assert "curlimages/curl@sha256:" in smoke
    assert "app.kubernetes.io/name: m5-staging-smoke" in smoke
    profile_evidence = 'printf \'%s\\n\' "$DEPLOYMENT_PROFILE" > "$raw_dir/deployment-profile.txt"'
    assert profile_evidence in deploy
    assert deploy.index(profile_evidence) < deploy.index("scripts/sanitize_deployment_evidence.py")
    rollback_script = (ROOT / "scripts/rollback_release.py").read_text(encoding="utf-8")
    assert "--to-revision" in rollback_script
    assert "--dry-run=server" in rollback_script


def test_tiny_staging_runbook_separates_live_yaml_documents() -> None:
    runbook = (ROOT / "docs/ops/tiny-staging-runbook.md").read_text(encoding="utf-8")
    namespace_export = (
        "kubectl get namespace enterprise-doc-agent-staging -o yaml \\\n"
        '  > "$workdir/live-prerequisites.yaml"'
    )
    separator = "printf '\\n---\\n' >> \"$workdir/live-prerequisites.yaml\""
    inventory_export = "kubectl -n enterprise-doc-agent-staging get \\\n"

    assert runbook.index(namespace_export) < runbook.index(separator)
    assert runbook.index(separator) < runbook.index(inventory_export, runbook.index(separator))


def test_staging_failure_evidence_collects_worker_logs_before_sanitizing() -> None:
    deploy = (ROOT / ".github/workflows/deploy-staging.yml").read_text(encoding="utf-8")
    expected = (
        ("worker-current.log", "enterprise-doc-worker", ""),
        ("worker-previous.log", "enterprise-doc-worker", "--previous=true"),
        ("consumer-current.log", "enterprise-doc-consumer", ""),
        ("consumer-previous.log", "enterprise-doc-consumer", "--previous=true"),
    )
    sanitizer = deploy.index("scripts/sanitize_deployment_evidence.py")
    upload = deploy.index("actions/upload-artifact@")
    for filename, label, extra in expected:
        command = f"capture {filename} kubectl -n enterprise-doc-agent-staging logs"
        position = deploy.index(command)
        assert position < sanitizer < upload
        snippet = deploy[position:sanitizer]
        assert f"app.kubernetes.io/name={label}" in snippet
        assert "--all-containers=true" in snippet
        assert "--prefix=true" in snippet
        assert "--tail=500" in snippet
        if extra:
            assert extra in snippet
    assert "trap 'rm -rf \"$raw_dir\"' EXIT" in deploy
    assert deploy.index("capture embedding-rollout-job.json") < sanitizer
    assert deploy.index("capture embedding-rollout.log") < sanitizer
    assert "staging-embedding-rollout.json" in deploy[:sanitizer]
    uploaded = deploy[upload:]
    assert "staging-sanitized-${{ github.sha }}" in uploaded
    assert "staging-raw-${{ github.sha }}" not in uploaded


def test_staging_workflows_preserve_admin_boundary_and_clean_credentials() -> None:
    deploy_workflow, deploy_steps = _deploy_workflow()
    assert deploy_workflow["jobs"]["deploy"]["timeout-minutes"] == 90
    assert "$HOME/.kube/config" not in str(deploy_workflow)
    assert deploy_steps[0]["name"] == "Validate staging dispatch gate"
    assert "STAGING_CONTROL_PLANE_APPROVED" in str(deploy_steps[0]["env"])
    assert '!= "true"' in str(deploy_steps[0]["run"])
    configure = _named_step(deploy_steps, "Configure cluster credentials")
    configure_run = str(configure["run"])
    assert "umask 077" in configure_run
    assert "$RUNNER_TEMP/enterprise-doc-kubeconfig-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}" in (
        configure_run
    )
    assert '>> "$GITHUB_ENV"' in configure_run
    assert "trap 'rm -f \"$KUBECONFIG\"' ERR INT TERM" in configure_run
    assert "kubectl auth can-i get namespace/enterprise-doc-agent-staging" in configure_run
    assert "kubectl auth can-i get namespaces | grep -qx 'yes'" not in configure_run
    assert "if kubectl auth can-i get secrets" in configure_run
    assert "if kubectl auth can-i create pods" in configure_run
    assert "if kubectl auth can-i update configmaps" in configure_run
    assert "if kubectl auth can-i patch networkpolicies.networking.k8s.io" in configure_run
    assert "kubectl auth can-i patch persistentvolumeclaims" in configure_run
    assert "kubectl auth can-i create jobs.batch" in configure_run
    assert "kubectl auth can-i patch deployments.apps" in configure_run
    assert "kubectl auth can-i patch deployments.apps/scale" in configure_run

    prerequisites = _named_step(
        deploy_steps,
        "Verify administrator-owned staging prerequisites",
    )
    prerequisite_run = str(prerequisites["run"])
    assert (
        "configmaps,persistentvolumeclaims,serviceaccounts,services,poddisruptionbudgets.policy"
        in prerequisite_run
    )
    assert "networkpolicies.networking.k8s.io" in prerequisite_run
    assert "staging-prerequisites-live.yaml" in prerequisite_run
    assert "printf '\\n---\\n'" in prerequisite_run
    assert "--live-manifest" in prerequisite_run
    assert "validate_staging_prerequisites.py" in prerequisite_run
    assert 'kubectl apply -f "$RUNNER_TEMP/staging-prerequisites.yaml"' not in prerequisite_run
    assert 'staging-migration.yaml"' not in prerequisite_run

    migration = _named_step(deploy_steps, "Apply migration job before rollout")
    migration_run = str(migration["run"])
    assert "previous migration Job is not complete" in migration_run
    assert '@.type=="Complete"' in migration_run
    assert migration_run.index("delete job enterprise-doc-migrate") < migration_run.index(
        "apply --dry-run=server"
    )
    assert migration_run.index("apply --dry-run=server") < migration_run.index(
        'kubectl apply -f "$RUNNER_TEMP/staging-migration.yaml"'
    )
    assert "attempt < 552" in migration_run
    assert 'test "$complete" = "True"' in migration_run
    assert 'test "$failed" = "True"' in migration_run
    assert "migration Job status after" in migration_run
    assert "migration Job did not complete" in migration_run
    assert "kubectl -n enterprise-doc-agent-staging get job/enterprise-doc-migrate" in (
        migration_run
    )
    assert "kubectl -n enterprise-doc-agent-staging describe job/enterprise-doc-migrate" in (
        migration_run
    )
    assert "--field-selector involvedObject.name=enterprise-doc-migrate" in migration_run
    assert migration_run.index('kubectl apply -f "$RUNNER_TEMP/staging-migration.yaml"') < (
        migration_run.index("migration Job status after")
    )
    assert migration_run.index("migration Job status after") < migration_run.index(
        'test "$migration_result" != "complete"'
    )
    assert migration_run.endswith("  exit 1\nfi\n")

    rollout = _named_step(deploy_steps, "Wait for workloads")
    rollout_run = str(rollout["run"])
    for deployment in ("api", "worker", "consumer", "web"):
        assert f"deployment/enterprise-doc-{deployment} --timeout=600s" in rollout_run

    embedding = _named_step(deploy_steps, "Run embedding provider and reindex gate")
    embedding_run = str(embedding["run"])
    assert embedding["env"]["EXPECTED_EMBEDDING_VERSION"] == (
        "${{ vars.STAGING_EMBEDDING_VERSION || '2' }}"
    )
    step_names = [str(step.get("name", "")) for step in deploy_steps]
    assert (
        step_names.index("Wait for workloads")
        < step_names.index("Run embedding provider and reindex gate")
        < step_names.index("Run in-cluster readiness smoke")
    )
    assert "previous embedding rollout Job is not complete" in embedding_run
    assert "DEPLOYMENT_PROFILE" in embedding["env"]
    assert "scale" in embedding_run
    assert (
        'test "${DEPLOYMENT_PROFILE:-}" = "tiny-single-node" || '
        'test "${DEPLOYMENT_PROFILE:-}" = "single-node-4c4g"' in embedding_run
    )
    scale_start = embedding_run.index("kubectl -n enterprise-doc-agent-staging scale")
    scale_end = embedding_run.index("--replicas=0", scale_start)
    embedding_scale = embedding_run[scale_start:scale_end]
    assert "deployment/enterprise-doc-api" in embedding_scale
    assert "deployment/enterprise-doc-web" in embedding_scale
    assert "deployment/enterprise-doc-worker" not in embedding_scale
    assert "deployment/enterprise-doc-consumer" not in embedding_scale
    assert "Keep the queue publisher and consumer available" in embedding_run
    assert "deployment/enterprise-doc-consumer --replicas=4" in embedding_run
    assert "rollout status" in embedding_run
    assert "deployment/enterprise-doc-consumer --timeout=180s" in embedding_run
    assert "restore_workloads" in embedding_run
    assert "trap - EXIT" in embedding_run
    assert "staging-embedding-rollout.yaml" in embedding_run
    assert "apply --dry-run=server" in embedding_run
    assert "attempt < 660" in embedding_run
    assert '@.type=="Complete"' in embedding_run
    assert '@.type=="Failed"' in embedding_run
    assert 'if test "$job_result" != "complete"' in embedding_run
    assert "validate_embedding_rollout_report.py" in embedding_run
    assert '--expected-version "$EXPECTED_EMBEDDING_VERSION"' in embedding_run
    assert "kubectl exec" not in embedding_run

    migration = _named_step(deploy_steps, "Apply migration job before rollout")
    migration_run = str(migration["run"])
    assert migration["env"]["DEPLOYMENT_PROFILE"] == (
        "${{ vars.STAGING_DEPLOYMENT_PROFILE || 'single-node-4c4g' }}"
    )
    assert (
        'test "$DEPLOYMENT_PROFILE" = "tiny-single-node" || '
        'test "$DEPLOYMENT_PROFILE" = "single-node-4c4g"' in migration_run
    )
    assert "--replicas=0" in migration_run

    restore = _named_step(deploy_steps, "Restore tiny workloads after deployment attempt")
    assert str(restore["if"]) == "always()"
    assert restore["env"]["DEPLOYMENT_PROFILE"] == (
        "${{ vars.STAGING_DEPLOYMENT_PROFILE || 'single-node-4c4g' }}"
    )
    assert "staging-workloads.yaml" in str(restore["run"])

    smoke_cleanup = _named_step(deploy_steps, "Clean up readiness smoke")
    assert "always()" in str(smoke_cleanup["if"])
    assert "delete job m5-staging-smoke" in str(smoke_cleanup["run"])

    deploy_cleanup = _named_step(deploy_steps, "Clean up cluster credentials")
    assert str(deploy_cleanup["if"]) == "always()"
    assert "RUNNER_TEMP/enterprise-doc-kubeconfig" in str(deploy_cleanup["run"])

    rollback_workflow = yaml.safe_load(
        (ROOT / ".github/workflows/rollback.yml").read_text(encoding="utf-8")
    )
    assert "$HOME/.kube/config" not in str(rollback_workflow)
    rollback_steps = rollback_workflow["jobs"]["rollback"]["steps"]
    assert rollback_steps[0]["name"] == "Validate staging dispatch gate"
    assert "STAGING_CONTROL_PLANE_APPROVED" in str(rollback_steps[0]["env"])
    assert '!= "true"' in str(rollback_steps[0]["run"])
    rollback_configure = _named_step(rollback_steps, "Configure cluster credentials")
    rollback_configure_run = str(rollback_configure["run"])
    assert "umask 077" in rollback_configure_run
    assert "$RUNNER_TEMP/enterprise-doc-kubeconfig-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}" in (
        rollback_configure_run
    )
    assert '>> "$GITHUB_ENV"' in rollback_configure_run
    assert "trap 'rm -f \"$KUBECONFIG\"' ERR INT TERM" in rollback_configure_run
    assert "if kubectl auth can-i get secrets" in rollback_configure_run
    assert "if kubectl auth can-i create pods" in rollback_configure_run
    rollback_cleanup = _named_step(rollback_steps, "Clean up cluster credentials")
    assert str(rollback_cleanup["if"]) == "always()"
    assert "RUNNER_TEMP/enterprise-doc-kubeconfig" in str(rollback_cleanup["run"])


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
    assert "validate_buildkit_provenance.py" in provenance_command
    assert "predicate_type=" in provenance_command
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
    assert '--type "$PROVENANCE_PREDICATE_TYPE"' in signature_command
    assert "steps.provenance.outputs.predicate_type" in str(signature["env"])

    verification = _named_step(steps, "Verify release attestations")
    assert "steps.sign.outcome == 'success'" in str(verification["if"])
    verification_command = str(verification["run"])
    assert "cosign verify" in verification_command
    assert "cosign verify-attestation" in verification_command
    assert "--type spdxjson" in verification_command
    assert '--type "$PROVENANCE_PREDICATE_TYPE"' in verification_command
    assert "steps.provenance.outputs.predicate_type" in str(verification["env"])
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
        assert trivy["with"]["severity"] == "CRITICAL,HIGH"
        assert trivy["with"]["limit-severities-for-sarif"] is True

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
        "buildkit-provenance-${{ matrix.name }}.type.txt",
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


def test_staging_image_relay_binds_the_versioned_canonical_receiver() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "relay-staging-images.yml").read_text(encoding="utf-8")
    )
    export = workflow["jobs"]["export"]
    matrix = export["strategy"]["matrix"]["include"]
    assert {entry["name"] for entry in matrix} == {"api", "worker", "consumer", "web"}
    steps = [step for step in export["steps"] if isinstance(step, dict)]
    archive_export = _named_step(steps, "Export digest-preserving OCI archive")
    assert "skopeo copy --all" in str(archive_export["run"])
    relay_upload = _named_step(steps, "Upload OCI archive through temporary R2 relay")
    receipt = str(relay_upload["run"])
    assert "receiver_script=scripts/import_staging_oci_archive.py" in receipt
    assert "receiver_base_name=$RELAY_ID" in receipt
    assert "receiver_canonical_base=docker.io/library/$RELAY_ID" in receipt
    assert "receiver_image_reference=$IMAGE_REF" in receipt
    assert (ROOT / "scripts" / "import_staging_oci_archive.py").is_file()
