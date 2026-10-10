"""Host-local fencing for a single, bounded staging maintenance window.

The supervisor and runner must use the same local SQLite file, never a network
filesystem or copies of the file. This coordinates trusted operators; it is not
an authorization boundary against an administrator who bypasses the guard.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

HEARTBEAT_MAX_AGE = 10.0
TERMINAL = {"migration_claimed", "restored", "blocked"}


class GuardError(ValueError):
    """The window cannot safely grant a mutation or automatic restoration."""


@dataclass(frozen=True)
class Tick:
    boot_id: str
    elapsed: float
    wall: float


def host_clock() -> Tick:
    # CLOCK_BOOTTIME includes host suspension; monotonic() alone does not on Linux.
    if sys.platform != "linux":
        raise GuardError("live maintenance supervision requires Linux CLOCK_BOOTTIME")
    return Tick(
        Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        time.clock_gettime(time.CLOCK_BOOTTIME),
        time.time(),
    )


@dataclass(frozen=True)
class Target:
    operation: str
    executor: str
    namespace_uid: str
    plan_sha256: str

    def validate(self) -> None:
        if not (
            re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", self.operation)
            and re.fullmatch(r"[a-f0-9]{40}", self.executor)
            and re.fullmatch(r"[A-Za-z0-9-]{1,64}", self.namespace_uid)
            and re.fullmatch(r"[a-f0-9]{64}", self.plan_sha256)
        ):
            raise GuardError("invalid maintenance target")


class Guard:
    def __init__(self, path: Path, *, clock: Callable[[], Tick] | None = None) -> None:
        self.path = path.absolute()
        self.clock = clock or host_clock

    def arm(self, target: Target, *, timeout: float = 1500, recovery_budget: float = 300) -> None:
        target.validate()
        if not (0 < timeout <= 1500 and 0 < recovery_budget <= 300):
            raise GuardError("window exceeds the pre-migration or recovery budget")
        tick = self.clock()
        if not tick.boot_id or not all(math.isfinite(v) for v in (tick.elapsed, tick.wall)):
            raise GuardError("invalid host clock")
        # Never silently recreate, reset, extend or reuse an existing window.
        descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        with sqlite3.connect(self.path) as connection:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                "CREATE TABLE guard (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT)"
            )
            connection.execute(
                "INSERT INTO guard VALUES (1, ?)",
                (
                    json.dumps(
                        {
                            "schema_version": 1,
                            "target": asdict(target),
                            "phase": "armed",
                            "boot_id": tick.boot_id,
                            "started": tick.elapsed,
                            "started_wall": tick.wall,
                            "deadline": tick.elapsed + timeout,
                            "recover_by": tick.elapsed + timeout + recovery_budget,
                            "heartbeat": None,
                            "last_tick": tick.elapsed,
                        }
                    ),
                ),
            )

    @contextmanager
    def _transaction(self) -> Iterator[dict[str, Any]]:
        if self.path.is_symlink() or not self.path.is_file():
            raise GuardError("maintenance guard is missing or not a regular file")
        connection = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=1)
        try:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT value FROM guard WHERE id=1").fetchone()
            if row is None:
                raise GuardError("maintenance guard has no state")
            state = json.loads(row[0])
            if not isinstance(state, dict) or state.get("schema_version") != 1:
                raise GuardError("unsupported maintenance guard state")
            if state.get("phase") not in TERMINAL | {"armed", "administering", "recovering"}:
                raise GuardError("unknown maintenance ownership state")
            Target(**state["target"]).validate()
            yield state
            connection.execute("UPDATE guard SET value=? WHERE id=1", (json.dumps(state),))
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _clock_valid(state: dict[str, Any], tick: Tick) -> bool:
        return (
            state["boot_id"] == tick.boot_id
            and math.isfinite(tick.elapsed)
            and math.isfinite(tick.wall)
            and tick.elapsed >= state["last_tick"]
        )

    def _allowed(self, state: dict[str, Any], target: Target) -> None:
        target.validate()
        tick = self.clock()
        if state["target"] != asdict(target) or state["phase"] != "armed":
            raise GuardError("maintenance target or ownership does not match")
        if not self._clock_valid(state, tick) or tick.elapsed >= state["deadline"]:
            raise GuardError("maintenance window expired or host clock changed")
        heartbeat = state["heartbeat"]
        if heartbeat is None or not 0 <= tick.elapsed - heartbeat <= HEARTBEAT_MAX_AGE:
            raise GuardError("maintenance supervisor heartbeat is missing or stale")
        state["last_tick"] = tick.elapsed

    def status(self) -> dict[str, Any]:
        with self._transaction() as state:
            return dict(state)

    def claim(self, target: Target) -> None:
        with self._transaction() as state:
            self._allowed(state, target)
            state["phase"] = "migration_claimed"

    def administer(self, target: Target, action: Callable[[float], None]) -> None:
        # Persist intent BEFORE a cluster request: process death / RPC timeout
        # does not mean the API server discarded the write. Unknown outcomes
        # cannot return to armed and race a delayed patch against restoration.
        with self._transaction() as state:
            self._allowed(state, target)
            until = min(float(state["deadline"]), self.clock().elapsed + 30)
            state.update(phase="administering", administration_until=until)
        try:
            action(until)
        except Exception:
            with self._transaction() as state:
                if state["phase"] == "administering":
                    state.update(phase="blocked", reason="administrator_result_unknown")
            raise
        with self._transaction() as state:
            tick = self.clock()
            if state["phase"] != "administering":
                raise GuardError("administrator operation lost ownership")
            if not self._clock_valid(state, tick) or tick.elapsed >= until:
                state.update(phase="blocked", reason="administrator_result_unknown")
            else:
                state.update(phase="armed", last_tick=tick.elapsed)
            blocked = state["phase"] == "blocked"
        if blocked:
            raise GuardError("administrator operation exceeded its known-result deadline")

    def poll(self) -> str:
        with self._transaction() as state:
            phase = str(state["phase"])
            if phase in TERMINAL:
                return phase
            tick = self.clock()
            if not self._clock_valid(state, tick):
                state.update(phase="blocked", reason="host_clock_changed")
            elif phase == "administering":
                if tick.elapsed >= state["administration_until"]:
                    state.update(phase="blocked", reason="administrator_result_unknown")
                else:
                    state.update(heartbeat=tick.elapsed, last_tick=tick.elapsed)
            elif tick.elapsed >= state["recover_by"]:
                state.update(phase="blocked", reason="recovery_budget_exhausted")
            else:
                state["heartbeat"] = tick.elapsed
                state["last_tick"] = tick.elapsed
                if tick.elapsed >= state["deadline"]:
                    state["phase"] = "recovering"
            return str(state["phase"])

    def recover(self, action: Callable[[float], None]) -> str:
        # poll commits recovering BEFORE cluster commands. A killed supervisor
        # can only resume recovery; it can never grant a migration afterwards.
        with self._transaction() as state:
            if state["phase"] != "recovering":
                raise GuardError("recovery does not own this window")
            tick = self.clock()
            if not self._clock_valid(state, tick) or tick.elapsed >= state["recover_by"]:
                state.update(phase="blocked", reason="recovery_clock_or_budget")
            else:
                try:
                    action(float(state["recover_by"]))
                    finished = self.clock()
                    if (
                        not self._clock_valid(state, finished)
                        or finished.elapsed >= state["recover_by"]
                    ):
                        raise GuardError("recovery exceeded budget")
                except Exception:
                    # Do not persist subprocess/DB exceptions, which can contain credentials.
                    state.update(phase="blocked", reason="recovery_failed")
                else:
                    state.update(phase="restored", restored_wall=finished.wall)
            return str(state["phase"])


def supervise(guard: Guard, restore: Callable[[float], None], *, interval: float = 2) -> str:
    if not 0 < interval <= 2:
        raise GuardError("invalid heartbeat interval")
    while True:
        try:
            phase = guard.poll()
        except sqlite3.OperationalError as error:
            if error.sqlite_errorcode not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                raise
            time.sleep(interval)
            continue
        if phase == "recovering":
            return guard.recover(restore)
        if phase in TERMINAL:
            return phase
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("arm", "supervise", "pause-web", "pause-backends", "stage", "claim", "status"):
        command = commands.add_parser(name)
        command.add_argument("--state", type=Path, required=True)
        if name not in {"status"}:
            command.add_argument("--plan", type=Path, required=True)
            command.add_argument("--plan-sha256", required=True)
        if name == "claim":
            command.add_argument("--operation", required=True)
            command.add_argument("--executor", required=True)
            command.add_argument("--namespace-uid", required=True)
            command.add_argument("--candidate", type=Path, required=True)
        if name == "arm":
            command.add_argument("--timeout", type=float, default=1500)
            command.add_argument("--recovery-budget", type=float, default=300)
    args = parser.parse_args()
    try:
        guard = Guard(args.state)
        if args.command == "status":
            print(json.dumps(guard.status(), sort_keys=True))
            return
        try:
            from scripts.maintenance_guard_cluster import Cluster, Plan
        except ModuleNotFoundError:
            from maintenance_guard_cluster import (  # type: ignore[import-not-found,no-redef]
                Cluster,
                Plan,
            )
        raw = args.plan.read_bytes()
        if hashlib.sha256(raw).hexdigest() != args.plan_sha256:
            raise GuardError("maintenance plan fingerprint changed")
        plan = Plan(json.loads(raw))
        target = Target(plan.operation, plan.executor, plan.namespace_uid, args.plan_sha256)
        if args.command == "claim":
            supplied = Target(args.operation, args.executor, args.namespace_uid, args.plan_sha256)
            if supplied != target:
                raise GuardError("workflow target differs from the maintenance plan")
            plan.validate_candidate(args.candidate)
            guard.claim(supplied)
        elif args.command == "arm":
            Cluster(plan).check_original(host_clock().elapsed + 30)
            guard.arm(target, timeout=args.timeout, recovery_budget=args.recovery_budget)
        else:
            if guard.status()["target"] != asdict(target):
                raise GuardError("state and maintenance plan differ")
            cluster = Cluster(plan)
            if args.command == "supervise":
                phase = supervise(guard, cluster.restore)
                print(json.dumps({"phase": phase}))
                if phase == "blocked":
                    raise SystemExit(1)
            else:
                action = {
                    "pause-web": cluster.pause_web,
                    "pause-backends": cluster.pause_backends,
                    "stage": cluster.stage,
                }[args.command]
                guard.administer(target, action)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, subprocess.SubprocessError):
        parser.exit(1, "maintenance guard refused; inspect private state and approved plan\n")


if __name__ == "__main__":
    main()
