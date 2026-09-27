from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest


def test_timeout_and_migration_compete_for_one_durable_owner(tmp_path: Path) -> None:
    from scripts.maintenance_guard import Guard, GuardError, Target, Tick

    now = [Tick("boot-one", 10.0, 1000.0)]
    target = Target("window-one", "a" * 40, "namespace-uid", "b" * 64)
    guard = Guard(tmp_path / "guard.db", clock=lambda: now[0])
    guard.arm(target, timeout=20, recovery_budget=10)
    with pytest.raises(GuardError, match="heartbeat"):
        guard.claim(target)
    assert guard.poll() == "armed"
    guard.claim(target)
    now[0] = Tick("boot-one", 35.0, 1025.0)
    assert guard.poll() == "migration_claimed"
    with pytest.raises(GuardError):
        guard.claim(target)

    other = Guard(tmp_path / "expired.db", clock=lambda: now[0])
    other.arm(target, timeout=20, recovery_budget=10)
    other.poll()
    now[0] = Tick("boot-one", 55.0, 1045.0)
    assert other.poll() == "recovering"
    with pytest.raises(GuardError):
        other.claim(target)
    assert Guard(other.path, clock=lambda: now[0]).status()["phase"] == "recovering"
    mutations: list[float] = []
    with pytest.raises(GuardError):
        other.administer(target, mutations.append)
    assert mutations == []


@pytest.mark.parametrize(
    "change",
    ["operation", "executor", "namespace_uid", "plan_sha256", "expired", "stale", "reboot"],
)
def test_invalid_claim_never_runs_administrator_action(tmp_path: Path, change: str) -> None:
    from scripts.maintenance_guard import Guard, GuardError, Target, Tick

    now = [Tick("boot", 0, 1000)]
    target = Target("window", "a" * 40, "namespace", "b" * 64)
    guard = Guard(tmp_path / "guard.db", clock=lambda: now[0])
    guard.arm(target, timeout=30)
    guard.poll()
    if change in {"expired", "stale"}:
        now[0] = Tick("boot", 30 if change == "expired" else 11, 1011)
    elif change == "reboot":
        now[0] = Tick("new-boot", 1, 1001)
    else:
        target = replace(
            target,
            **{
                change: {
                    "operation": "other",
                    "executor": "c" * 40,
                    "namespace_uid": "other",
                    "plan_sha256": "d" * 64,
                }[change]
            },
        )
    actions: list[float] = []
    with pytest.raises(GuardError):
        guard.claim(target)
    with pytest.raises(GuardError):
        guard.administer(target, actions.append)
    assert not actions
    assert guard.status()["phase"] == "armed"


def test_real_sqlite_race_has_exactly_one_winner(tmp_path: Path) -> None:
    from scripts.maintenance_guard import Guard, GuardError, Target, Tick

    target = Target("window", "a" * 40, "namespace", "b" * 64)
    for index in range(12):
        path = tmp_path / f"race-{index}.db"
        guard = Guard(path, clock=lambda: Tick("boot", 0, 1000))
        guard.arm(target, timeout=20)
        Guard(path, clock=lambda: Tick("boot", 10, 1010)).poll()
        barrier = threading.Barrier(2)

        def claim(path: Path = path, barrier: threading.Barrier = barrier) -> str:
            barrier.wait(timeout=3)
            try:
                Guard(path, clock=lambda: Tick("boot", 19.9, 1019.9)).claim(target)
            except GuardError:
                return "rejected"
            return "claimed"

        def recover(path: Path = path, barrier: threading.Barrier = barrier) -> str:
            barrier.wait(timeout=3)
            return Guard(path, clock=lambda: Tick("boot", 20, 1020)).poll()

        with ThreadPoolExecutor(max_workers=2) as pool:
            left, right = pool.submit(claim), pool.submit(recover)
            assert (left.result(), right.result()) in {
                ("claimed", "migration_claimed"),
                ("rejected", "recovering"),
            }


def test_interrupted_recovery_resumes_but_failure_and_expired_budget_block(tmp_path: Path) -> None:
    from scripts.maintenance_guard import Guard, GuardError, Target, Tick

    tick = [Tick("boot", 0, 1000)]
    guard = Guard(tmp_path / "guard.db", clock=lambda: tick[0])
    target = Target("window", "a" * 40, "namespace", "b" * 64)
    guard.arm(target, timeout=5, recovery_budget=5)
    tick[0] = Tick("boot", 5, -2000)  # wall-clock corrections cannot extend the window
    assert guard.poll() == "recovering"

    def interrupted(deadline: float) -> None:
        assert deadline == 10
        raise SystemExit(17)

    with pytest.raises(SystemExit):
        guard.recover(interrupted)
    fresh = Guard(guard.path, clock=lambda: tick[0])
    assert fresh.status()["phase"] == "recovering"
    assert fresh.recover(lambda deadline: None) == "restored"
    with pytest.raises(GuardError):
        fresh.claim(target)
    with pytest.raises(FileExistsError):
        fresh.arm(target)

    other = Guard(tmp_path / "other.db", clock=lambda: tick[0])
    other.arm(target, timeout=1, recovery_budget=2)
    tick[0] = Tick("boot", 6, 1)
    other.poll()

    def fail(deadline: float) -> None:
        raise ValueError("secret upstream detail")

    assert other.recover(fail) == "blocked"
    assert "secret" not in str(other.status())
    assert other.poll() == "blocked"

    expired = Guard(tmp_path / "expired.db", clock=lambda: tick[0])
    expired.arm(target, timeout=1, recovery_budget=1)
    tick[0] = Tick("boot", 8, 3)
    assert expired.poll() == "blocked"


def test_detached_supervisor_finishes_after_launcher_has_exited(tmp_path: Path) -> None:
    from scripts.maintenance_guard import Guard, Target, Tick

    root = Path(__file__).resolve().parents[2]
    state, result = tmp_path / "guard.db", tmp_path / "restored.json"
    guard = Guard(state, clock=lambda: Tick("fixture-boot", time.monotonic(), time.time()))
    guard.arm(Target("window", "a" * 40, "namespace", "b" * 64), timeout=2, recovery_budget=8)
    worker = tmp_path / "supervisor.py"
    worker.write_text(
        "import sys,time,json\nfrom pathlib import Path\n"
        "from scripts.maintenance_guard import Guard,Tick,supervise\n"
        "state,result=map(Path,sys.argv[1:])\n"
        "guard=Guard(state,clock=lambda:Tick('fixture-boot',time.monotonic(),time.time()))\n"
        "restore=lambda deadline:result.write_text(json.dumps({'restored':True}))\n"
        "phase=supervise(guard,restore,interval=0.05)\n"
        "sys.exit(0 if phase=='restored' else 1)\n",
        encoding="utf-8",
    )
    launcher = (
        "import subprocess,sys,os,json\n"
        "options={'start_new_session':True}\n"
        "if os.name=='nt':\n"
        " flags=subprocess.DETACHED_PROCESS|subprocess.CREATE_NEW_PROCESS_GROUP\n"
        " options={'creationflags':flags}\n"
        "p=subprocess.Popen(sys.argv[1:],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**options)\n"
        "print(p.pid)\n"
    )
    environment = {**os.environ, "PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1"}
    launched = subprocess.run(
        [sys.executable, "-c", launcher, sys.executable, str(worker), str(state), str(result)],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    assert int(launched.stdout) > 0  # launcher has ended; child is independently alive
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and guard.status()["phase"] not in {"restored", "blocked"}:
        time.sleep(0.05)
    assert guard.status()["phase"] == "restored"
    assert result.read_text() == '{"restored": true}'


def test_missing_corrupt_or_unknown_state_cannot_create_a_new_window(tmp_path: Path) -> None:
    from scripts.maintenance_guard import Guard, GuardError, Target, Tick

    missing = tmp_path / "missing.db"
    with pytest.raises(GuardError):
        Guard(missing).poll()
    assert not missing.exists()
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"corrupt evidence")
    with pytest.raises(sqlite3.DatabaseError):
        Guard(corrupt).poll()
    guard = Guard(tmp_path / "guard.db", clock=lambda: Tick("boot", 0, 1000))
    guard.arm(Target("window", "a" * 40, "namespace", "b" * 64))
    state = guard.status()
    state["phase"] = "unknown-migration-result"
    with sqlite3.connect(guard.path) as connection:
        connection.execute("UPDATE guard SET value=?", (json.dumps(state),))
    with pytest.raises(GuardError, match="unknown"):
        guard.poll()


def test_interrupted_administrator_request_never_looks_like_an_idle_window(tmp_path: Path) -> None:
    from scripts.maintenance_guard import Guard, GuardError, Target, Tick

    now = [Tick("boot", 0, 1000)]
    guard = Guard(tmp_path / "guard.db", clock=lambda: now[0])
    target = Target("window", "a" * 40, "namespace", "b" * 64)
    guard.arm(target, timeout=50)
    guard.poll()

    def interrupted(deadline: float) -> None:
        # A killed kubectl client does not prove its API request never committed.
        raise SystemExit(17)

    with pytest.raises(SystemExit):
        guard.administer(target, interrupted)
    assert guard.status()["phase"] == "administering"
    with pytest.raises(GuardError):
        guard.claim(target)
    now[0] = Tick("boot", 31, 1031)
    assert guard.poll() == "blocked"
    assert guard.status()["reason"] == "administrator_result_unknown"


@pytest.mark.parametrize("result", ["success", "failure", "late"])
def test_administrator_ownership_requires_a_timely_success_receipt(
    tmp_path: Path, result: str
) -> None:
    from scripts.maintenance_guard import Guard, GuardError, Target, Tick

    now = [Tick("boot", 0, 1000)]
    guard = Guard(tmp_path / "guard.db", clock=lambda: now[0])
    target = Target("window", "a" * 40, "namespace", "b" * 64)
    guard.arm(target, timeout=50)
    guard.poll()

    def action(deadline: float) -> None:
        assert deadline == 30
        assert guard.poll() == "administering"
        with pytest.raises(GuardError):
            guard.claim(target)
        if result == "failure":
            raise TimeoutError("indeterminate API request")
        now[0] = Tick("boot", 31 if result == "late" else 1, 1001)

    if result == "success":
        guard.administer(target, action)
        assert guard.status()["phase"] == "armed"
    else:
        with pytest.raises((GuardError, TimeoutError)):
            guard.administer(target, action)
        assert guard.status()["phase"] == "blocked"
