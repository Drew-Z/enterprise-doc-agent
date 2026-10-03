"""Bounded 0031 release switching; no migrations, business smoke, or supplier calls.

Private plans contain the two primary API key values needed for rollback. Keep
them out of Git/logs, and explicitly approve any temporary remote runtime copy.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from scripts.maintenance_guard import GuardError, Target, Tick, host_clock
from scripts.maintenance_guard_cluster import (
    APPLICATIONS,
    NAMESPACE,
    Cluster,
    Plan,
    Run,
    approval_annotations,
    canonical_digest,
    database_revision,
    kubectl,
    selected,
    validate_objects,
)

PREFIX = "enterprise-doc-agent/"
FENCE = PREFIX + "release-fence"
SECRET_KEY = "MODEL__API_KEY"
EXECUTOR_FILES = (
    "release_switch.py",
    "maintenance_guard.py",
    "maintenance_guard_cluster.py",
    "backup_database.py",
    "render_k8s_phase.py",
    "validate_staging_prerequisites.py",
)
CONFIG_KEYS = {
    "API__QUEUE_OBSERVATION_ENABLED",
    "MODEL__BASE_URL",
    "MODEL__MODEL_NAME",
    "MODEL__MODEL_VERSION",
    "MODEL__FALLBACK_MODEL_NAME",
    "MODEL__REASONING_EFFORT",
    "MODEL__FALLBACK_REASONING_EFFORT",
    "MODEL__TIMEOUT_SECONDS",
    "PRESALES__MODEL_ROUTE",
    "PRESALES__BACKGROUND_GENERATION_ENABLED",
    "PRESALES__AUTOMATIC_FAILOVER_ENABLED",
    "PRESALES__CONCURRENT_ATTEMPT_LIMIT",
}
APPROVAL_KEYS = {
    PREFIX + "approved-" + name
    for name in (
        "api-images",
        "worker-images",
        "consumer-images",
        "web-images",
        "config-sha256",
        "model-base-url",
        "model-name",
        "model-fallback-name",
    )
} | {PREFIX + "prerequisites-sha256"}


class ReleaseNotStarted(GuardError):
    """The current apply call failed before any cluster mutation was attempted."""


def normalize_api_pool(source: dict[str, Any], target: dict[str, Any], pool_size: int) -> None:
    """Validate the declared API-only pool override, then restore baseline for comparison."""
    expected = {"DATABASE__POOL_SIZE": str(pool_size), "DATABASE__MAX_OVERFLOW": "0"}
    for container in (source, target):
        entries = container.get("env", [])
        if not isinstance(entries, list) or any(
            not isinstance(entry, dict) or not isinstance(entry.get("name"), str)
            for entry in entries
        ):
            raise GuardError("invalid API environment entries")
        if len({entry["name"] for entry in entries}) != len(entries):
            raise GuardError("duplicate API environment entries")
    actual = [entry for entry in target.get("env", []) if entry["name"] in expected]
    if sorted(actual, key=lambda entry: entry["name"]) != [
        {"name": name, "value": expected[name]} for name in sorted(expected)
    ]:
        raise GuardError("API pool environment differs from declared bounded override")
    if [entry for entry in source.get("env", []) if entry["name"] not in expected] != [
        entry for entry in target.get("env", []) if entry["name"] not in expected
    ]:
        raise GuardError("unrelated API environment changed")
    if "env" in source:
        target["env"] = copy.deepcopy(source["env"])
    else:
        target.pop("env", None)


class ReleasePlan(Plan):
    def __init__(self, value: dict[str, Any]) -> None:
        self.data = copy.deepcopy(value)
        self.operation = value["operation"]
        self.executor = value["executor"]
        self.namespace_uid = value["namespace_uid"]
        Target(self.operation, self.executor, self.namespace_uid, "0" * 64).validate()
        if value["schema_version"] != 2 or value["original_revision"] != "20260924_0031":
            raise GuardError("release switching requires the reviewed 0031 schema")
        self.revision = value["original_revision"]
        self.original = self.data["original_prerequisites"]
        self.candidate = self.data["candidate_prerequisites"]
        self.deployments = self.data["deployments"]
        self.desired: list[dict[str, Any]] = self.data["candidate_deployments"]
        self.jobs = self.data["jobs"]
        self.secret: dict[str, str] = self.data["secret"]
        self.old_namespace = selected(self.original, "Namespace", NAMESPACE)
        self.new_namespace = selected(self.candidate, "Namespace", NAMESPACE)
        self.old_config = selected(self.original, "ConfigMap", "enterprise-doc-config")
        self.new_config = selected(self.candidate, "ConfigMap", "enterprise-doc-config")
        if self.old_namespace["metadata"].get("uid") != self.namespace_uid:
            raise GuardError("Namespace identity does not match")
        if any(i.get("kind") == "Secret" for i in self.original + self.candidate):
            raise GuardError("full Secrets must not enter release prerequisites")
        old, new = self.old_config["data"], self.new_config["data"]
        changed = {k for k in old.keys() | new.keys() if old.get(k) != new.get(k)}
        if changed - CONFIG_KEYS or any(not isinstance(v, str) for v in new.values()):
            raise GuardError("configuration exceeds the reviewed release scope")
        for key in ("MODEL__REASONING_EFFORT", "MODEL__FALLBACK_REASONING_EFFORT"):
            if key in new and new[key] not in {"low", "medium", "high", "xhigh"}:
                raise GuardError("invalid model reasoning effort")
        if "MODEL__FALLBACK_MODEL_NAME" in changed:
            fallback_name = new.get("MODEL__FALLBACK_MODEL_NAME", "")
            if not fallback_name or fallback_name != fallback_name.strip():
                raise GuardError("invalid fallback model name")
        before, after = (
            approval_annotations(self.old_namespace),
            approval_annotations(self.new_namespace),
        )
        if (
            before.keys() != after.keys()
            or {k for k in before if before[k] != after[k]} - APPROVAL_KEYS
        ):
            raise GuardError("Namespace approval exceeds release scope")
        for config, approved_values in ((old, before), (new, after)):
            if approved_values[PREFIX + "approved-config-sha256"] != canonical_digest(config):
                raise GuardError("configuration approval fingerprint does not match")
            for suffix, key in (("base-url", "BASE_URL"), ("name", "MODEL_NAME")):
                if approved_values[PREFIX + "approved-model-" + suffix] != config["MODEL__" + key]:
                    raise GuardError("model route approval does not match")
            if config.get("MODEL__FALLBACK_MODEL_NAME") != approved_values.get(
                PREFIX + "approved-model-fallback-name"
            ):
                raise GuardError("fallback model approval does not match")
        if (
            new.get("PRESALES__AUTOMATIC_FAILOVER_ENABLED") == "true"
            and new.get("PRESALES__BACKGROUND_GENERATION_ENABLED") != "true"
        ):
            raise GuardError("automatic failover requires background generation")
        for key in (
            "API__QUEUE_OBSERVATION_ENABLED",
            "PRESALES__BACKGROUND_GENERATION_ENABLED",
            "PRESALES__AUTOMATIC_FAILOVER_ENABLED",
        ):
            if key in new and new[key] not in {"true", "false"}:
                raise GuardError("invalid background feature setting")
        if (
            "PRESALES__CONCURRENT_ATTEMPT_LIMIT" in changed
            and new.get("PRESALES__CONCURRENT_ATTEMPT_LIMIT") != "1"
        ):
            raise GuardError("single-node release requires one background attempt")
        projected = copy.deepcopy(self.candidate)
        selected(projected, "ConfigMap", "enterprise-doc-config")["data"] = old
        selected(projected, "Namespace", NAMESPACE)["metadata"]["annotations"] = before
        validate_objects(self.original, projected)
        validate_objects(self.candidate, self.candidate)
        if len(self.deployments) != 4 or len(self.desired) != 4:
            raise GuardError("exactly four application deployments are required")
        if "api_database_pool_size" in self.data:
            pool_size = self.data["api_database_pool_size"]
            if type(pool_size) is not int or not 1 <= pool_size <= 4:
                raise GuardError("API pool override must be an integer from one to four")
        for name in APPLICATIONS:
            original = selected(self.deployments, "Deployment", name)
            candidate = selected(self.desired, "Deployment", name)
            if (
                original["metadata"].get("namespace") != NAMESPACE
                or not original["metadata"].get("uid")
                or original["spec"].get("replicas") != 1
                or candidate["metadata"] != original["metadata"]
            ):
                raise GuardError("unexpected deployment identity")
            normalized = copy.deepcopy(candidate["spec"])
            source = original["spec"]["template"]
            target = normalized["template"]
            if len(source["spec"]["containers"]) != 1 or len(target["spec"]["containers"]) != 1:
                raise GuardError("single reviewed application container required")
            old_image = source["spec"]["containers"][0]["image"]
            new_image = target["spec"]["containers"][0]["image"]
            for image in (old_image, new_image):
                if not re.fullmatch(r"ghcr.io/drew-z/" + name + r"@sha256:[0-9a-f]{64}", image):
                    raise GuardError("immutable application image required")
            image_key = PREFIX + "approved-" + name.removeprefix("enterprise-doc-") + "-images"
            approved = after[image_key]
            retained_images = (
                new_image == old_image
                and approved == before[image_key]
                and old_image in approved.split(",")
            )
            if set(approved.split(",")) != {old_image, new_image} and not retained_images:
                raise GuardError("candidate and rollback images must both be approved")
            target["spec"]["containers"][0]["image"] = old_image
            if name == "enterprise-doc-api" and "api_database_pool_size" in self.data:
                normalize_api_pool(
                    source["spec"]["containers"][0],
                    target["spec"]["containers"][0],
                    self.data["api_database_pool_size"],
                )
            old_annotations = source.get("metadata", {}).get("annotations", {})
            new_annotations = target.get("metadata", {}).get("annotations", {})
            if new_annotations.get(PREFIX + "config-sha256") != canonical_digest(new):
                raise GuardError("candidate template configuration fingerprint differs")
            if PREFIX + "config-sha256" in old_annotations:
                new_annotations[PREFIX + "config-sha256"] = old_annotations[
                    PREFIX + "config-sha256"
                ]
            else:
                new_annotations.pop(PREFIX + "config-sha256", None)
                if not new_annotations:
                    target.get("metadata", {}).pop("annotations", None)
            if normalized != original["spec"]:
                raise GuardError("workload change exceeds declared release scope")
        if any(set(job) != {"name", "uid"} or not all(job.values()) for job in self.jobs):
            raise GuardError("invalid baseline job inventory")
        if (
            set(self.secret) != {"uid", "old_key", "new_key", "other_data_sha256"}
            or not self.secret["uid"]
            or not re.fullmatch(r"[0-9a-f]{64}", self.secret["other_data_sha256"])
        ):
            raise GuardError("invalid primary-key recovery binding")
        for key in ("old_key", "new_key"):
            try:
                decoded = base64.b64decode(self.secret[key], validate=True)
            except ValueError:
                raise GuardError("invalid primary-key encoding") from None
            if not decoded:
                raise GuardError("primary-key must not be empty")

    def accepted_specs(self, expected: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            expected["spec"],
            selected(self.desired, "Deployment", expected["metadata"]["name"])["spec"],
        ]


class ReleaseCluster(Cluster):
    plan: ReleasePlan

    def __init__(
        self,
        plan: ReleasePlan,
        *,
        run: Run = kubectl,
        revision: Callable[[float], str] = database_revision,
        clock: Callable[[], float] | None = None,
        idle: Callable[[float], bool] | None = None,
    ) -> None:
        super().__init__(plan, run=run, revision=revision, clock=clock)
        self.idle = idle or database_idle

    def _check_fence(self, item: dict[str, Any]) -> None:
        fence = item["metadata"].get("annotations", {}).get(FENCE)
        if fence is not None and not re.fullmatch(self.plan.operation + r":[0-9a-f]{32}", fence):
            raise GuardError("another release owns a resource fence")

    def _prerequisites(self, deadline: float) -> list[dict[str, Any]]:
        items = super()._prerequisites(deadline)
        for item in items:
            self._check_fence(item)
            annotations = item["metadata"].get("annotations", {})
            annotations.pop(FENCE, None)
            if not annotations:
                item["metadata"].pop("annotations", None)
        return items

    def _secret(self, deadline: float) -> dict[str, Any]:
        item = self._get("secret", deadline, "enterprise-doc-secrets")
        self._check_fence(item)
        data = dict(item["data"])
        key = data.pop(SECRET_KEY, None)
        if (
            item["metadata"].get("uid") != self.plan.secret["uid"]
            or key not in (self.plan.secret["old_key"], self.plan.secret["new_key"])
            or canonical_digest(data) != self.plan.secret["other_data_sha256"]
        ):
            raise GuardError("Secret identity or unrelated credential changed")
        return item

    def _inspect(self, deadline: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        result = super()._inspect(deadline)
        for item in result[1]:
            self._check_fence(item)
        self._secret(deadline)
        return result

    def _patch(
        self, item: dict[str, Any], kind: str, path: str, value: object, deadline: float
    ) -> None:
        metadata = item["metadata"]
        if not metadata.get("resourceVersion") or not metadata.get("uid"):
            raise GuardError("resource identity or version missing")
        annotations = dict(metadata.get("annotations", {}))
        annotations[FENCE] = self.plan.operation + ":" + uuid.uuid4().hex
        # A real metadata change also fences a late RPC when the requested value
        # already matches; Kubernetes may not advance resourceVersion on a no-op.
        if path == "/metadata/annotations":
            if not isinstance(value, dict):
                raise GuardError("invalid annotation patch")
            annotations = dict(value)
            annotations[FENCE] = self.plan.operation + ":" + uuid.uuid4().hex
        patch = [
            {"op": "test", "path": "/metadata/uid", "value": metadata["uid"]},
            {
                "op": "test",
                "path": "/metadata/resourceVersion",
                "value": metadata["resourceVersion"],
            },
            {"op": "add", "path": "/metadata/annotations", "value": annotations},
        ]
        if path != "/metadata/annotations":
            patch.append({"op": "replace", "path": path, "value": value})
        args = ["patch", kind, metadata["name"], "--type=json", "--patch-file=/dev/stdin"]
        if kind != "namespace":
            args += ["-n", NAMESPACE]
        self.run(args, json.dumps(patch), self._remaining(deadline))

    def _wait_paused(self, deadline: float) -> None:
        while True:
            _, deployments = self._inspect(deadline)
            pending = any(
                any(
                    item.get("status", {}).get(k, 0)
                    for k in ("replicas", "readyReplicas", "availableReplicas", "updatedReplicas")
                )
                for item in deployments
                if item["metadata"]["name"] in APPLICATIONS
            )
            pods = self._items("pods", deadline)
            active = any(
                item.get("status", {}).get("phase") not in {"Succeeded", "Failed"}
                and item.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/name")
                in APPLICATIONS
                for item in pods
            )
            if not pending and not active:
                return
            time.sleep(min(0.2, self._remaining(deadline)))

    def _close(self, deadline: float) -> None:
        self._inspect(deadline)  # Refuse drift before the first mutation.
        for name in (APPLICATIONS[-1], *APPLICATIONS[:-1]):
            _, deployments = self._inspect(deadline)
            self._patch(
                selected(deployments, "Deployment", name),
                "deployment",
                "/spec/replicas",
                0,
                deadline,
            )
        self._wait_paused(deadline)

    def _bundle(self, candidate: bool, deadline: float) -> None:
        desired = self.plan.candidate if candidate else self.plan.original
        deployments = self.plan.desired if candidate else self.plan.deployments
        for kind, name, field in (
            ("ConfigMap", "enterprise-doc-config", "/data"),
            ("Namespace", NAMESPACE, "/metadata/annotations"),
        ):
            live, _ = self._inspect(deadline)
            item, expected = selected(live, kind, name), selected(desired, kind, name)
            value = expected["data"] if kind == "ConfigMap" else approval_annotations(expected)
            self._patch(item, kind.lower(), field, value, deadline)
        secret = self._secret(deadline)
        self._patch(
            secret,
            "secret",
            "/data/" + SECRET_KEY,
            self.plan.secret["new_key" if candidate else "old_key"],
            deadline,
        )
        for expected in deployments:
            _, current = self._inspect(deadline)
            item = selected(current, "Deployment", expected["metadata"]["name"])
            spec = copy.deepcopy(expected["spec"])
            spec["replicas"] = 0
            self._patch(item, "deployment", "/spec", spec, deadline)

    def _open(self, deadline: float) -> None:
        for name in APPLICATIONS:
            _, current = self._inspect(deadline)
            self._patch(
                selected(current, "Deployment", name), "deployment", "/spec/replicas", 1, deadline
            )
            while True:
                remaining = self._remaining(deadline)
                try:
                    self.run(
                        [
                            "rollout",
                            "status",
                            "deployment/" + name,
                            "-n",
                            NAMESPACE,
                            f"--timeout={remaining:.3f}s",
                        ],
                        None,
                        remaining,
                    )
                except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
                    time.sleep(min(0.2, self._remaining(deadline)))
                else:
                    break

    def _clear_fences(self, deadline: float) -> None:
        resources = [
            ("namespace", NAMESPACE),
            ("configmap", "enterprise-doc-config"),
            ("secret", "enterprise-doc-secrets"),
            *(("deployment", name) for name in APPLICATIONS),
        ]
        for kind, name in resources:
            item = self._get(kind, deadline, name)
            self._check_fence(item)
            metadata = item["metadata"]
            annotations = dict(metadata.get("annotations", {}))
            if FENCE not in annotations:
                continue
            del annotations[FENCE]
            patch = [
                {"op": "test", "path": "/metadata/uid", "value": metadata["uid"]},
                {
                    "op": "test",
                    "path": "/metadata/resourceVersion",
                    "value": metadata["resourceVersion"],
                },
                {"op": "replace", "path": "/metadata/annotations", "value": annotations},
            ]
            args = ["patch", kind, name, "--type=json", "--patch-file=/dev/stdin"]
            if kind != "namespace":
                args += ["-n", NAMESPACE]
            self.run(args, json.dumps(patch), self._remaining(deadline))

    def verify(self, candidate: bool, deadline: float) -> None:
        live, deployments = self._inspect(deadline)
        desired = self.plan.candidate if candidate else self.plan.original
        expected_deployments = self.plan.desired if candidate else self.plan.deployments
        validate_objects(desired, live)
        for expected in expected_deployments:
            actual = selected(deployments, "Deployment", expected["metadata"]["name"])
            if actual["spec"] != expected["spec"]:
                raise GuardError("release workload verification failed")
        if (
            self._secret(deadline)["data"][SECRET_KEY]
            != self.plan.secret["new_key" if candidate else "old_key"]
        ):
            raise GuardError("release primary-key verification failed")

    def check_original(self, deadline: float) -> None:
        self.verify(False, deadline)
        if not self.idle(self._remaining(deadline)):
            raise GuardError("active business operations must finish before switching")

    def apply(self, deadline: float) -> None:
        try:
            self.check_original(deadline)
        except Exception:
            raise ReleaseNotStarted(
                "release preflight rejected: active business or configuration drift"
            ) from None
        self._close(deadline)
        # Re-check after closing the public entry and stopping all application
        # processes. A race with a final user submission must abort this switch.
        if not self.idle(self._remaining(deadline)):
            raise GuardError("business operations arrived while closing the entry")
        self._bundle(True, deadline)
        self._open(deadline)
        self.verify(True, deadline)
        self._clear_fences(deadline)
        validate_objects(self.plan.candidate, super()._prerequisites(deadline))

    def restore(self, deadline: float) -> None:
        self._close(deadline)
        self._bundle(False, deadline)
        self._open(deadline)
        self.verify(False, deadline)
        self._clear_fences(deadline)
        validate_objects(self.plan.original, super()._prerequisites(deadline))


def database_idle(timeout: float) -> bool:
    from scripts.backup_database import postgres_process_environment

    url = os.environ.get("MAINTENANCE_GUARD_DATABASE_URL")
    if not url:
        raise GuardError("read-only database connection is unavailable")
    environment = postgres_process_environment(url)
    for key in ("MAINTENANCE_GUARD_DATABASE_URL", "PGHOSTADDR", "PGSERVICEFILE"):
        environment.pop(key, None)
    environment.update(
        PGCONNECT_TIMEOUT="5",
        PGPASSFILE=os.devnull,
        PGOPTIONS="-c default_transaction_read_only=on -c statement_timeout=2000",
    )
    query = """BEGIN READ ONLY; SET LOCAL statement_timeout='2000ms';
    SELECT NOT (
      EXISTS (SELECT 1 FROM public.jobs WHERE status NOT IN ('succeeded','dead','cancelled'))
      OR EXISTS (SELECT 1 FROM public.presales_attempts
                 WHERE state NOT IN ('succeeded','failed','expired'))
      OR EXISTS (SELECT 1 FROM public.agent_runs
                 WHERE status NOT IN ('succeeded','failed','refused','cancelled'))
      OR EXISTS (SELECT 1 FROM public.usage_reservations
                 WHERE state NOT IN ('consumed','released'))
      OR EXISTS (SELECT 1 FROM public.product_usage_reservations
                 WHERE state NOT IN ('consumed','released'))
      OR EXISTS (SELECT 1 FROM public.upload_sessions
                 WHERE status IN ('initializing','active','completing') AND expires_at > now())
    ); COMMIT;"""
    result = subprocess.run(
        [
            "psql",
            "--no-psqlrc",
            "--no-password",
            "--tuples-only",
            "--no-align",
            "--quiet",
            "--set=ON_ERROR_STOP=1",
            "--command",
            query,
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    )
    if result.stdout.strip() not in {"t", "f"}:
        raise GuardError("database idle result is invalid")
    return result.stdout.strip() == "t"


@contextmanager
def exclusive(path: Path) -> Iterator[None]:
    # OS locks are released on process death. The host service must use
    # KillMode=control-group so a restarted executor has no surviving children.
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


class Switch:
    def __init__(self, path: Path, *, clock: Callable[[], Tick] = host_clock) -> None:
        self.path, self.clock = path.absolute(), clock

    def _save(self, state: dict[str, Any]) -> None:
        temporary = self.path.with_name(self.path.name + ".write-" + uuid.uuid4().hex)
        try:
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write((json.dumps(state, sort_keys=True) + "\n").encode())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            if sys.platform != "win32":
                descriptor = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        finally:
            if temporary.exists():
                temporary.unlink()

    def status(self) -> dict[str, Any]:
        if self.path.is_symlink() or not self.path.is_file():
            raise GuardError("release state must be an existing regular file")
        state: dict[str, Any] = json.loads(self.path.read_bytes())
        if state.get("schema_version") != 1 or state.get("phase") not in {
            "armed",
            "applying",
            "recovering",
            "succeeded",
            "restored",
            "blocked",
        }:
            raise GuardError("invalid release state")
        Target(**state["target"]).validate()
        return state

    def arm(self, target: Target, *, timeout: float = 600, recovery_budget: float = 300) -> None:
        target.validate()
        if not (0 < timeout <= 600 and 0 < recovery_budget <= 300):
            raise GuardError("release budget exceeds ten minutes plus five minutes recovery")
        tick = self.clock()
        if not tick.boot_id or not all(math.isfinite(v) for v in (tick.elapsed, tick.wall)):
            raise GuardError("invalid host clock")
        with exclusive(self.path.with_suffix(".lock")):
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
            self._save(
                {
                    "schema_version": 1,
                    "target": asdict(target),
                    "phase": "armed",
                    "boot_id": tick.boot_id,
                    "started": tick.elapsed,
                    "started_wall": tick.wall,
                    "last_tick": tick.elapsed,
                    "deadline": tick.elapsed + timeout,
                    "recover_by": tick.elapsed + timeout + recovery_budget,
                    "recovery_budget": recovery_budget,
                }
            )

    def execute(
        self, target: Target, *, apply: Callable[[float], None], restore: Callable[[float], None]
    ) -> dict[str, Any]:
        target.validate()
        with exclusive(self.path.with_suffix(".lock")):
            state = self.status()
            if state["target"] != asdict(target):
                raise GuardError("release state and plan differ")
            if state["phase"] in {"succeeded", "restored", "blocked"}:
                return state
            tick = self.clock()
            if (
                tick.boot_id != state["boot_id"]
                or not math.isfinite(tick.elapsed)
                or tick.elapsed < state["last_tick"]
                or tick.elapsed >= state["recover_by"]
            ):
                state.update(phase="blocked", reason="host_clock_or_budget")
                self._save(state)
                return state
            if state["phase"] == "armed" and tick.elapsed >= state["deadline"]:
                state.update(phase="blocked", reason="expired_before_start")
                self._save(state)
                return state
            if state["phase"] == "armed" and tick.elapsed < state["deadline"]:
                state.update(phase="applying", last_tick=tick.elapsed)
                self._save(state)
                try:
                    apply(float(state["deadline"]))
                    finished = self.clock()
                    if (
                        finished.boot_id != state["boot_id"]
                        or not state["last_tick"] <= finished.elapsed < state["deadline"]
                    ):
                        raise GuardError("release exceeded deadline")
                except ReleaseNotStarted:
                    # This live process knows no write was attempted. Do not
                    # interrupt work that arrived after arm by starting rollback.
                    state.update(phase="blocked", reason="preflight_rejected")
                    self._save(state)
                    return state
                except Exception:
                    # The raw exception may include credentials. Never persist it.
                    state["reason"] = "apply_failed"
                else:
                    state.update(phase="succeeded", finished_wall=finished.wall)
                    self._save(state)
                    return state
            tick = self.clock()
            if tick.elapsed < state["last_tick"]:
                state.update(phase="blocked", reason="host_clock_or_budget")
                self._save(state)
                return state
            if "recovery_deadline" not in state:
                state["recovery_deadline"] = min(
                    state["recover_by"], tick.elapsed + state["recovery_budget"]
                )
            state.update(phase="recovering", last_tick=tick.elapsed)
            self._save(state)
            try:
                if (
                    tick.boot_id != state["boot_id"]
                    or not math.isfinite(tick.elapsed)
                    or tick.elapsed >= state["recovery_deadline"]
                ):
                    raise GuardError("recovery deadline or clock invalid")
                restore(float(state["recovery_deadline"]))
                finished = self.clock()
                if (
                    finished.boot_id != state["boot_id"]
                    or not tick.elapsed <= finished.elapsed < state["recovery_deadline"]
                ):
                    raise GuardError("recovery exceeded deadline")
            except Exception:
                state.update(phase="blocked", reason="recovery_failed")
            else:
                state.update(phase="restored", finished_wall=finished.wall)
            self._save(state)
            return state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "arm", "execute", "status"))
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--state", type=Path)
    args = parser.parse_args()
    try:
        raw = args.plan.read_bytes()
        if hashlib.sha256(raw).hexdigest() != args.plan_sha256:
            raise GuardError("plan fingerprint changed")
        plan = ReleasePlan(json.loads(raw))
        sources = plan.data.get("executor_sources", {})
        if set(sources) != set(EXECUTOR_FILES) or any(
            hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() != sources[name]
            for name in EXECUTOR_FILES
        ):
            raise GuardError("executor source bundle differs from approved plan")
        if args.command == "validate":
            print(json.dumps({"status": "valid", "revision": plan.revision}))
            return
        if args.state is None:
            raise GuardError("state path is required")
        switch = Switch(args.state)
        target = Target(plan.operation, plan.executor, plan.namespace_uid, args.plan_sha256)
        if args.command == "status":
            result = switch.status()
        else:
            cluster = ReleaseCluster(plan)

            def initialize_database(deadline: float) -> None:
                secret = cluster._secret(deadline)
                os.environ["MAINTENANCE_GUARD_DATABASE_URL"] = base64.b64decode(
                    secret["data"]["DATABASE__URL"]
                ).decode()

            def apply(deadline: float) -> None:
                initialize_database(deadline)
                cluster.apply(deadline)

            def restore(deadline: float) -> None:
                initialize_database(deadline)
                cluster.restore(deadline)

            if args.command == "arm":
                initialize_database(host_clock().elapsed + 10)
                cluster.check_original(host_clock().elapsed + 30)
                switch.arm(target)
                result = switch.status()
            else:
                result = switch.execute(target, apply=apply, restore=restore)
        print(json.dumps(result, sort_keys=True))
        if result["phase"] in {"restored", "blocked"}:
            raise SystemExit(1)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        parser.exit(1, "release switch refused; inspect the private plan and state\n")


if __name__ == "__main__":
    main()
