"""Fixed, bounded cluster actions for the staging maintenance guard."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

try:
    from scripts.backup_database import postgres_process_environment
    from scripts.maintenance_guard import GuardError, Target, host_clock
    from scripts.render_k8s_phase import select_phase
    from scripts.validate_staging_prerequisites import validate_prerequisites
except ModuleNotFoundError:
    from backup_database import (  # type: ignore[import-not-found,no-redef]
        postgres_process_environment,
    )
    from maintenance_guard import (  # type: ignore[import-not-found,no-redef]
        GuardError,
        Target,
        host_clock,
    )
    from render_k8s_phase import select_phase  # type: ignore[import-not-found,no-redef]
    from validate_staging_prerequisites import (  # type: ignore[import-not-found,no-redef]
        validate_prerequisites,
    )

NAMESPACE = "enterprise-doc-agent-staging"
APPLICATIONS = tuple(f"enterprise-doc-{name}" for name in ("api", "worker", "consumer", "web"))
CONFIG_ADDITIONS = {
    "PRESALES__AUTOMATIC_FAILOVER_ENABLED": "false",
    "PRESALES__BACKGROUND_GENERATION_ENABLED": "false",
    "PRESALES__DAILY_DISPATCH_LIMIT": "200",
    "PRESALES__QUEUE_TIMEOUT_SECONDS": "900",
    "PRESALES__ROUTE_COOLDOWN_SECONDS": "30",
    "PRESALES__ROUTE_FAILURE_THRESHOLD": "3",
}
ANNOTATIONS = {
    "enterprise-doc-agent/approved-" + suffix
    for suffix in ("api-images", "worker-images", "consumer-images", "web-images", "config-sha256")
} | {"enterprise-doc-agent/prerequisites-sha256"}
PREREQUISITE_KINDS = (
    "configmaps,persistentvolumeclaims,serviceaccounts,services,"
    "poddisruptionbudgets.policy,ingresses.networking.k8s.io,networkpolicies.networking.k8s.io"
)
Run = Callable[[list[str], str | None, float], str]


def canonical_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def validate_objects(expected: list[dict[str, Any]], live: list[dict[str, Any]]) -> None:
    # Reuse the production validator, including all 17 Namespace approval keys.
    # These files are temporary parser inputs, not recovery baselines.
    namespace = next(item for item in live if item["kind"] == "Namespace")
    with tempfile.TemporaryDirectory(prefix="maintenance-validation-") as directory:
        root = Path(directory)
        for name, value in (("expected", expected), ("live", live)):
            (root / f"{name}.yaml").write_text(yaml.safe_dump_all(value), encoding="utf-8")
        (root / "namespace.json").write_text(json.dumps(namespace), encoding="utf-8")
        validate_prerequisites(root / "expected.yaml", root / "namespace.json", root / "live.yaml")


def selected(items: list[dict[str, Any]], kind: str, name: str) -> dict[str, Any]:
    found = [
        item
        for item in items
        if item.get("kind") == kind and item.get("metadata", {}).get("name") == name
    ]
    if len(found) != 1:
        raise GuardError("maintenance resource is missing or duplicated")
    return found[0]


def approval_annotations(item: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = dict(item["metadata"]["annotations"])
    result.pop("kubectl.kubernetes.io/last-applied-configuration", None)
    return result


class Plan:
    def __init__(self, value: dict[str, Any]) -> None:
        self.data = copy.deepcopy(value)
        self.operation: str = value["operation"]
        self.executor: str = value["executor"]
        self.namespace_uid: str = value["namespace_uid"]
        Target(self.operation, self.executor, self.namespace_uid, "0" * 64).validate()
        self.original: list[dict[str, Any]] = self.data["original_prerequisites"]
        self.candidate: list[dict[str, Any]] = self.data["candidate_prerequisites"]
        self.deployments: list[dict[str, Any]] = self.data["deployments"]
        self.revision: str = self.data["original_revision"]
        if value["schema_version"] != 1 or not re.fullmatch(
            r"[a-f0-9]{64}", value["candidate_sha256"]
        ):
            raise GuardError("unsupported maintenance plan")
        if self.revision != "20260923_0027":
            raise GuardError("this recovery plan only supports the reviewed pre-migration revision")
        if any(item.get("kind") == "Secret" for item in self.original + self.candidate):
            raise GuardError("maintenance plans cannot contain Secrets")
        self.old_namespace = selected(self.original, "Namespace", NAMESPACE)
        self.new_namespace = selected(self.candidate, "Namespace", NAMESPACE)
        self.old_config = selected(self.original, "ConfigMap", "enterprise-doc-config")
        self.new_config = selected(self.candidate, "ConfigMap", "enterprise-doc-config")
        if self.old_namespace["metadata"].get("uid") != self.namespace_uid:
            raise GuardError("plan Namespace UID does not match")
        if CONFIG_ADDITIONS.keys() & self.old_config["data"].keys():
            raise GuardError("plan no longer describes the reviewed six additions")
        if self.new_config["data"] != self.old_config["data"] | CONFIG_ADDITIONS:
            raise GuardError("candidate configuration exceeds reviewed additions")
        old, new = (
            approval_annotations(self.old_namespace),
            approval_annotations(self.new_namespace),
        )
        if old.keys() != new.keys() or {k for k in old if old[k] != new[k]} != ANNOTATIONS:
            raise GuardError("candidate Namespace approval changes are incomplete or excessive")
        projected = copy.deepcopy(self.candidate)
        selected(projected, "ConfigMap", "enterprise-doc-config")["data"] = self.old_config["data"]
        selected(projected, "Namespace", NAMESPACE)["metadata"]["annotations"] = old
        validate_objects(self.original, projected)
        validate_objects(self.candidate, self.candidate)
        if len(self.deployments) != 4 or {i["metadata"]["name"] for i in self.deployments} != set(
            APPLICATIONS
        ):
            raise GuardError("recovery requires exactly four original applications")
        for item in self.deployments:
            if (
                item["kind"] != "Deployment"
                or item["metadata"].get("namespace") != NAMESPACE
                or not item["metadata"].get("uid")
                or item["spec"].get("replicas") != 1
            ):
                raise GuardError("unexpected original workload identity or replica count")
        self.jobs: list[dict[str, str]] = self.data["jobs"]
        if any(set(job) != {"name", "uid"} or not all(job.values()) for job in self.jobs):
            raise GuardError("invalid baseline job inventory")

    def validate_candidate(self, path: Path) -> None:
        try:
            documents = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
        except yaml.YAMLError as error:
            raise GuardError("rendered candidate is invalid") from error
        if canonical_digest(documents) != self.data["candidate_sha256"]:
            raise GuardError("rendered candidate differs from approved plan")
        validate_objects(self.candidate, select_phase(documents, "prerequisites"))

    def accepted_specs(self, expected: dict[str, Any]) -> list[dict[str, Any]]:
        return [expected["spec"]]


def kubectl(args: list[str], payload: str | None, timeout: float) -> str:
    result = subprocess.run(
        ["kubectl", f"--request-timeout={timeout:.3f}s", *args],
        input=payload,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    )
    return result.stdout


def database_revision(timeout: float) -> str:
    # Reuse the existing backup client's libpq environment adapter, without
    # invoking its backup functionality or installing a driver in the runner.
    url = os.environ.get("MAINTENANCE_GUARD_DATABASE_URL")
    if not url:
        raise GuardError("recovery database connection is unavailable")
    environment = postgres_process_environment(url)
    environment.pop("MAINTENANCE_GUARD_DATABASE_URL", None)
    environment.pop("PGHOSTADDR", None)
    environment.pop("PGSERVICEFILE", None)
    environment.update(
        PGCONNECT_TIMEOUT="5",
        PGPASSFILE=os.devnull,
        PGOPTIONS="-c default_transaction_read_only=on -c statement_timeout=2000",
    )
    command = [
        "psql",
        "--no-psqlrc",
        "--no-password",
        "--tuples-only",
        "--no-align",
        "--quiet",
        "--set=ON_ERROR_STOP=1",
        "--command",
        "BEGIN READ ONLY; SET LOCAL statement_timeout='2000ms'; "
        "SELECT version_num FROM public.alembic_version; COMMIT;",
    ]
    result = subprocess.run(
        command, env=environment, capture_output=True, text=True, timeout=timeout, check=True
    )
    return result.stdout.strip()


class Cluster:
    def __init__(
        self,
        plan: Plan,
        *,
        run: Run = kubectl,
        revision: Callable[[float], str] = database_revision,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.plan, self.run, self.revision = plan, run, revision
        self.clock = clock or (lambda: host_clock().elapsed)

    def _remaining(self, deadline: float) -> float:
        remaining = min(10.0, deadline - self.clock())
        if remaining <= 0:
            raise GuardError("cluster action exhausted its budget")
        return remaining

    def _get(self, resource: str, deadline: float, name: str | None = None) -> dict[str, Any]:
        args = ["get", resource, *([name] if name else [])]
        if resource != "namespace":
            args += ["-n", NAMESPACE]
        value = json.loads(self.run([*args, "-o", "json"], None, self._remaining(deadline)))
        if not isinstance(value, dict):
            raise GuardError("invalid live cluster inventory")
        return value

    def _items(self, resource: str, deadline: float) -> list[dict[str, Any]]:
        items = self._get(resource, deadline).get("items")
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise GuardError("incomplete live cluster inventory")
        return items

    def _prerequisites(self, deadline: float) -> list[dict[str, Any]]:
        namespace = self._get("namespace", deadline, NAMESPACE)
        if namespace.get("metadata", {}).get("uid") != self.plan.namespace_uid:
            raise GuardError("live Namespace identity changed")
        return [namespace, *self._items(PREREQUISITE_KINDS, deadline)]

    def _validate_mixed(self, live: list[dict[str, Any]]) -> None:
        projected = copy.deepcopy(live)
        config = selected(projected, "ConfigMap", "enterprise-doc-config")
        ns = selected(projected, "Namespace", NAMESPACE)
        if config["metadata"].get("uid") != self.plan.old_config["metadata"].get("uid"):
            raise GuardError("ConfigMap identity changed")
        if config.get("data") not in (self.plan.old_config["data"], self.plan.new_config["data"]):
            raise GuardError("unapproved configuration drift")
        if approval_annotations(ns) not in (
            approval_annotations(self.plan.old_namespace),
            approval_annotations(self.plan.new_namespace),
        ):
            raise GuardError("unapproved Namespace annotation drift")
        config["data"] = self.plan.old_config["data"]
        ns["metadata"]["annotations"] = approval_annotations(self.plan.old_namespace)
        validate_objects(self.plan.original, projected)

    def _inspect(self, deadline: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        live = self._prerequisites(deadline)
        self._validate_mixed(live)
        deployments = self._items("deployments", deadline)
        if (
            {i["metadata"]["name"] for i in deployments}
            - set(APPLICATIONS)
            - {"enterprise-doc-redis"}
        ):
            raise GuardError("unreviewed deployment in maintenance namespace")
        for expected in self.plan.deployments:
            item = selected(deployments, "Deployment", expected["metadata"]["name"])
            spec = copy.deepcopy(item["spec"])
            replicas = spec.pop("replicas", None)
            allowed = [copy.deepcopy(value) for value in self.plan.accepted_specs(expected)]
            for value in allowed:
                value.pop("replicas")
            if (
                item["metadata"].get("uid") != expected["metadata"]["uid"]
                or spec not in allowed
                or replicas not in (0, 1)
            ):
                raise GuardError("original workload identity or template changed")
        if self._items("hpa,cronjobs,statefulsets,daemonsets", deadline):
            raise GuardError("unreviewed controller can write during maintenance")
        jobs = self._items("jobs", deadline)
        identities = [
            {"name": item["metadata"]["name"], "uid": item["metadata"]["uid"]} for item in jobs
        ]
        if sorted(identities, key=lambda i: i["name"]) != sorted(
            self.plan.jobs, key=lambda i: i["name"]
        ):
            raise GuardError("migration/job inventory changed")
        for item in jobs:
            status = item.get("status", {})
            if status.get("active", 0) or not any(
                c.get("type") == "Complete" and c.get("status") == "True"
                for c in status.get("conditions", [])
            ):
                raise GuardError("migration/job state is not complete")
        for item in self._items("pods", deadline):
            if item.get("status", {}).get("phase") in {"Succeeded", "Failed"}:
                continue
            metadata = item.get("metadata", {})
            if metadata.get("labels", {}).get("app.kubernetes.io/name") not in {
                *APPLICATIONS,
                "enterprise-doc-redis",
            } or any(ref.get("kind") == "Job" for ref in metadata.get("ownerReferences", [])):
                raise GuardError("unreviewed active pod")
        if self.revision(self._remaining(deadline)) != self.plan.revision:
            raise GuardError("database migration revision changed or is unknown")
        return live, deployments

    def check_original(self, deadline: float) -> None:
        live, deployments = self._inspect(deadline)
        validate_objects(self.plan.original, live)
        if any(
            selected(deployments, "Deployment", name)["spec"]["replicas"] != 1
            for name in APPLICATIONS
        ):
            raise GuardError("arm before pausing the original applications")

    def _patch(
        self, item: dict[str, Any], kind: str, path: str, value: object, deadline: float
    ) -> None:
        metadata = item["metadata"]
        if not metadata.get("resourceVersion"):
            raise GuardError("resourceVersion is missing")
        patch = [
            {
                "op": "test",
                "path": "/metadata/resourceVersion",
                "value": metadata["resourceVersion"],
            },
            {"op": "replace", "path": path, "value": value},
        ]
        args = ["patch", kind, metadata["name"], "--type=json", "--patch-file=/dev/stdin"]
        if kind != "namespace":
            args += ["-n", NAMESPACE]
        self.run(args, json.dumps(patch), self._remaining(deadline))

    def _configure(
        self, live: list[dict[str, Any]], desired: list[dict[str, Any]], deadline: float
    ) -> None:
        for kind, name, field in (
            ("ConfigMap", "enterprise-doc-config", "/data"),
            ("Namespace", NAMESPACE, "/metadata/annotations"),
        ):
            current, expected = selected(live, kind, name), selected(desired, kind, name)
            old_value = current["data"] if kind == "ConfigMap" else approval_annotations(current)
            new_value = expected["data"] if kind == "ConfigMap" else approval_annotations(expected)
            if old_value != new_value:
                self._patch(current, kind.lower(), field, new_value, deadline)
        validate_objects(desired, self._prerequisites(deadline))

    def pause_web(self, deadline: float) -> None:
        live, deployments = self._inspect(deadline)
        validate_objects(self.plan.original, live)
        web = selected(deployments, "Deployment", "enterprise-doc-web")
        self._patch(web, "deployment", "/spec/replicas", 0, deadline)

    def pause_backends(self, deadline: float) -> None:
        live, deployments = self._inspect(deadline)
        validate_objects(self.plan.original, live)
        if selected(deployments, "Deployment", "enterprise-doc-web")["spec"]["replicas"] != 0:
            raise GuardError("close the public entry before stopping backends")
        for name in APPLICATIONS[:-1]:
            self._patch(
                selected(deployments, "Deployment", name),
                "deployment",
                "/spec/replicas",
                0,
                deadline,
            )

    def stage(self, deadline: float) -> None:
        live, deployments = self._inspect(deadline)
        for name in APPLICATIONS:
            item = selected(deployments, "Deployment", name)
            if item["spec"]["replicas"] != 0 or any(
                item.get("status", {}).get(k, 0)
                for k in ("replicas", "readyReplicas", "availableReplicas", "updatedReplicas")
            ):
                raise GuardError("all applications must be paused before staging configuration")
        if any(
            item.get("status", {}).get("phase") not in {"Succeeded", "Failed"}
            and item.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/name")
            != "enterprise-doc-redis"
            for item in self._items("pods", deadline)
        ):
            raise GuardError("active application pod remains")
        self._configure(live, self.plan.candidate, deadline)

    def restore(self, deadline: float) -> None:
        live, deployments = self._inspect(deadline)
        # Keep Web closed while reconciling configuration/backends. On a restarted
        # supervisor these replica operations are idempotent and revalidated.
        web = selected(deployments, "Deployment", "enterprise-doc-web")
        if web["spec"]["replicas"]:
            self._patch(web, "deployment", "/spec/replicas", 0, deadline)
        self._configure(live, self.plan.original, deadline)
        for name in APPLICATIONS:
            _, current = self._inspect(deadline)
            item = selected(current, "Deployment", name)
            if item["spec"]["replicas"] != 1:
                self._patch(item, "deployment", "/spec/replicas", 1, deadline)
            # Bounded by the overall recovery deadline, including process startup.
            while True:
                remaining = self._remaining(deadline)
                try:
                    self.run(
                        [
                            "rollout",
                            "status",
                            f"deployment/{name}",
                            "-n",
                            NAMESPACE,
                            f"--timeout={remaining:.3f}s",
                        ],
                        None,
                        remaining,
                    )
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                    # Poll readiness only; never repeat a mutation or supplier call.
                    time.sleep(min(0.2, self._remaining(deadline)))
                else:
                    break
        validate_objects(self.plan.original, self._prerequisites(deadline))
