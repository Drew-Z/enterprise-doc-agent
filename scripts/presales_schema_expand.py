"""Fixed additive 0032-to-0034 schema expansion with original applications retained."""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import math
import os
import queue
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import asdict
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol, TextIO

from scripts.backup_database import POSTGRES_IDENTIFIER, postgres_process_environment
from scripts.maintenance_guard import GuardError, Target, host_clock
from scripts.maintenance_guard_cluster import Run, kubectl, validate_objects
from scripts.release_switch import (
    EXECUTOR_FILES,
    ReleaseCluster,
    ReleaseNotStarted,
    ReleasePlan,
    Switch,
)

ORIGINAL_REVISION = "20261005_0032"
TARGET_REVISION = "20261008_0034"
DATABASE_LOCK = 7410862100330034
# The six statements emitted by Alembic upgrade 0032:0034. The executor owns
# the surrounding transaction and precondition checks; plans cannot supply SQL.
MIGRATION_BODY = """ALTER TABLE presales_reviews ADD COLUMN prerequisite_changes JSONB;
ALTER TABLE presales_reviews ADD CONSTRAINT ck_presales_reviews_presales_review_changes_object
CHECK (prerequisite_changes IS NULL OR jsonb_typeof(prerequisite_changes) = 'object');
UPDATE alembic_version SET version_num='20261008_0033'
WHERE alembic_version.version_num = '20261005_0032';
ALTER TABLE presales_attempts ADD COLUMN execution_policy JSONB;
ALTER TABLE presales_attempts ADD CONSTRAINT
ck_presales_attempts_presales_attempt_execution_policy_object
CHECK (execution_policy IS NULL OR jsonb_typeof(execution_policy) = 'object');
UPDATE alembic_version SET version_num='20261008_0034'
WHERE alembic_version.version_num = '20261008_0033';"""

IDLE_SQL = """SELECT NOT (
  EXISTS (SELECT 1 FROM jobs WHERE status NOT IN ('succeeded','dead','cancelled'))
  OR EXISTS (SELECT 1 FROM presales_attempts WHERE state NOT IN ('succeeded','failed','expired'))
  OR EXISTS (SELECT 1 FROM agent_runs
             WHERE status NOT IN ('succeeded','failed','refused','cancelled'))
  OR EXISTS (SELECT 1 FROM usage_reservations WHERE state NOT IN ('consumed','released'))
  OR EXISTS (SELECT 1 FROM product_usage_reservations WHERE state NOT IN ('consumed','released'))
  OR EXISTS (SELECT 1 FROM upload_sessions
             WHERE status IN ('initializing','active','completing') AND expires_at > now())
);"""


def _column_query(table: str, name: str) -> str:
    return f"""(SELECT json_build_object(
        'type', format_type(atttypid, atttypmod),
        'not_null', attnotnull, 'has_default', atthasdef)
        FROM pg_attribute WHERE attrelid='{table}'::regclass
        AND attname='{name}' AND NOT attisdropped)"""


def _constraint_query(table: str, name: str) -> str:
    return f"""(SELECT json_build_object('definition', pg_get_constraintdef(oid),
        'validated', convalidated) FROM pg_constraint
        WHERE conrelid='{table}'::regclass AND conname='{name}' AND contype='c')"""


REVIEW_CHECK = "ck_presales_reviews_presales_review_changes_object"
ATTEMPT_CHECK = "ck_presales_attempts_presales_attempt_execution_policy_object"
SCHEMA_SQL = f"""SELECT json_build_object(
    'revision', (SELECT min(version_num) FROM alembic_version),
    'version_count', (SELECT count(*) FROM alembic_version),
    'review_column', {_column_query("presales_reviews", "prerequisite_changes")},
    'attempt_column', {_column_query("presales_attempts", "execution_policy")},
    'review_constraint', {_constraint_query("presales_reviews", REVIEW_CHECK)},
    'attempt_constraint', {_constraint_query("presales_attempts", ATTEMPT_CHECK)}
);"""


class PsqlSession:
    """One credential-private session; its advisory lock outlives transaction commit."""

    def __init__(
        self,
        database_url: str,
        *,
        schema: str = "public",
        command: tuple[str, ...] = ("psql",),
        clock: Callable[[], float] = time.monotonic,
        connect_timeout: float = 10,
    ) -> None:
        if POSTGRES_IDENTIFIER.fullmatch(schema) is None:
            raise GuardError("invalid schema identifier")
        self.schema, self.command, self.clock = schema, command, clock
        self.connect_timeout = connect_timeout
        self.environment = postgres_process_environment(database_url)
        for key in ("MAINTENANCE_GUARD_DATABASE_URL", "PGHOSTADDR", "PGSERVICEFILE"):
            self.environment.pop(key, None)
        self.environment.update(
            PGCONNECT_TIMEOUT="5",
            PGPASSFILE=os.devnull,
            PGOPTIONS=(
                "-c statement_timeout=10000 -c lock_timeout=5000 -c idle_session_timeout=600000"
            ),
            PGAPPNAME="enterprise-doc-schema-expansion",
        )
        self.process: subprocess.Popen[str] | None = None
        self.reader: threading.Thread | None = None
        self.output: queue.Queue[str | None] = queue.Queue(maxsize=128)
        self.overflow = threading.Event()

    def _read(self, stream: TextIO) -> None:
        try:
            while True:
                line = stream.readline(65537)
                if len(line) > 65536:
                    self.overflow.set()
                    break
                self.output.put_nowait(line if line else None)
                if not line:
                    break
        except (OSError, ValueError, queue.Full):
            self.overflow.set()

    def __enter__(self) -> PsqlSession:
        deadline = self.clock() + self.connect_timeout
        try:
            self.process = subprocess.Popen(
                [
                    *self.command,
                    "--no-psqlrc",
                    "--no-password",
                    "--quiet",
                    "--tuples-only",
                    "--no-align",
                    "--set=ON_ERROR_STOP=1",
                ],
                env=self.environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
            assert self.process.stdout is not None
            self.reader = threading.Thread(
                target=self._read, args=(self.process.stdout,), daemon=True
            )
            self.reader.start()
            # Session poolers may ignore PGOPTIONS. Establish and read back the
            # limits over this connection before taking any migration lock.
            settings = self.query(
                "SET statement_timeout='10000ms';\n"
                "SET lock_timeout='5000ms';\n"
                "SET idle_session_timeout='600000ms';\n"
                f'SET search_path TO "{self.schema}";\n'
                "SELECT json_build_object(\n"
                "'statement_timeout', (SELECT setting::int FROM pg_settings "
                "WHERE name='statement_timeout'),\n"
                "'lock_timeout', (SELECT setting::int FROM pg_settings "
                "WHERE name='lock_timeout'),\n"
                "'idle_session_timeout', (SELECT setting::int FROM pg_settings "
                "WHERE name='idle_session_timeout'),\n"
                "'schema', current_schema());",
                deadline - self.clock(),
            )
            if json.loads(settings) != {
                "statement_timeout": 10000,
                "lock_timeout": 5000,
                "idle_session_timeout": 600000,
                "schema": self.schema,
            }:
                raise GuardError("database session limits were not established")
            self.query(
                f"SELECT pg_advisory_lock({DATABASE_LOCK});",
                deadline - self.clock(),
            )
            return self
        except Exception:
            self.close()
            raise GuardError("database session or migration lock unavailable") from None

    def query(self, sql: str, timeout: float) -> str:
        if not math.isfinite(timeout) or timeout <= 0 or timeout > 60:
            raise GuardError("database operation budget is invalid")
        process = self.process
        if process is None or process.stdin is None or process.poll() is not None:
            raise GuardError("database session is unavailable")
        marker = "schema_receipt_" + uuid.uuid4().hex
        deadline = self.clock() + timeout
        try:
            process.stdin.write(sql + "\n\\echo " + marker + "\n")
            process.stdin.flush()
            lines: list[str] = []
            size = 0
            while not self.overflow.is_set():
                remaining = deadline - self.clock()
                if remaining <= 0:
                    break
                line = self.output.get(timeout=remaining)
                if line is None:
                    break
                if line.strip() == marker:
                    return "".join(lines).strip()
                size += len(line)
                if size > 65536:
                    break
                lines.append(line)
        except (OSError, ValueError, queue.Empty):
            pass
        self.close()
        raise GuardError("database operation failed or acknowledgement is unknown")

    def revision(self, timeout: float) -> str:
        try:
            value = json.loads(self.query(SCHEMA_SQL, timeout))
        except (ValueError, TypeError):
            raise GuardError("database schema observation is invalid") from None
        if not isinstance(value, dict) or value.get("version_count") != 1:
            raise GuardError("database revision is missing or ambiguous")
        revision = value.get("revision")
        fields = ("review_column", "attempt_column", "review_constraint", "attempt_constraint")
        if revision == ORIGINAL_REVISION and all(value.get(name) is None for name in fields):
            return ORIGINAL_REVISION
        column = {"type": "jsonb", "not_null": False, "has_default": False}
        if revision == TARGET_REVISION and all(value.get(name) == column for name in fields[:2]):
            for name, field in (
                ("review_constraint", "prerequisite_changes"),
                ("attempt_constraint", "execution_policy"),
            ):
                constraint = value.get(name)
                expected = (
                    f"CHECK ((({field} IS NULL) OR (jsonb_typeof({field}) = 'object'::text)))"
                )
                if (
                    not isinstance(constraint, dict)
                    or constraint.get("validated") is not True
                    or constraint.get("definition") != expected
                ):
                    raise GuardError("expanded schema constraint differs from the fixed migration")
            return TARGET_REVISION
        raise GuardError("database revision or additive schema shape changed")

    def idle(self, timeout: float) -> bool:
        value = self.query(IDLE_SQL, timeout)
        if value not in {"t", "f"}:
            raise GuardError("database idle observation is invalid")
        return value == "t"

    def expand(self, timeout: float) -> None:
        if not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise GuardError("schema expansion budget is invalid")
        deadline = self.clock() + timeout

        def remaining() -> float:
            return min(10, deadline - self.clock())

        try:
            self.query(
                "BEGIN;\nLOCK TABLE alembic_version, presales_reviews, presales_attempts "
                "IN ACCESS EXCLUSIVE MODE;",
                remaining(),
            )
            if self.revision(remaining()) != ORIGINAL_REVISION or not self.idle(remaining()):
                raise GuardError("schema expansion preconditions changed")
            self.query(MIGRATION_BODY + "\nCOMMIT;", deadline - self.clock())
            if self.revision(remaining()) != TARGET_REVISION:
                raise GuardError("schema expansion verification failed")
        except Exception:
            # Closing rolls back an uncommitted transaction. A possibly committed
            # result is observed by recovery only after acquiring the same lock.
            self.close()
            raise

    def close(self) -> None:
        process = self.process
        if process is None:
            return
        if process.poll() is None:
            try:
                if process.stdin is not None:
                    process.stdin.write("\\q\n")
                    process.stdin.flush()
                process.wait(timeout=1)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                process.kill()
                process.wait(timeout=5)
        if self.reader is not None:
            self.reader.join(timeout=1)
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                # A confirmed exited psql may already have broken its input
                # pipe. Cleanup must not replace the bounded database error.
                with suppress(OSError, ValueError):
                    stream.close()
        self.process = None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class ExpansionPlan(ReleasePlan):
    def __init__(self, value: dict[str, Any]) -> None:
        required = {
            "schema_version",
            "release_kind",
            "operation",
            "executor",
            "namespace_uid",
            "original_revision",
            "target_revision",
            "migration_sha256",
            "original_prerequisites",
            "candidate_prerequisites",
            "deployments",
            "candidate_deployments",
            "jobs",
            "secret",
        }
        if not required <= value.keys() or value.keys() - required - {"executor_sources"}:
            raise GuardError("unsupported schema expansion plan fields")
        if (
            value["schema_version"] != 4
            or value["release_kind"] != "presales_schema_expand"
            or value["original_revision"] != ORIGINAL_REVISION
            or value["target_revision"] != TARGET_REVISION
            or value["migration_sha256"] != hashlib.sha256(MIGRATION_BODY.encode()).hexdigest()
        ):
            raise GuardError("schema expansion requires the fixed 0032-to-0034 migration")
        projected = copy.deepcopy(value)
        projected.update(schema_version=2, release_kind="images_only")
        super().__init__(projected)
        if self.original != self.candidate or self.deployments != self.desired:
            raise GuardError("schema expansion must retain every original application resource")
        self.data = copy.deepcopy(value)

    def accepted_revisions(self) -> tuple[str, ...]:
        return ORIGINAL_REVISION, TARGET_REVISION


class ExpansionDatabase(Protocol):
    def revision(self, timeout: float) -> str: ...
    def idle(self, timeout: float) -> bool: ...
    def expand(self, timeout: float) -> None: ...


class ExpansionCluster(ReleaseCluster):
    plan: ExpansionPlan

    def __init__(
        self,
        plan: ExpansionPlan,
        database: ExpansionDatabase,
        *,
        run: Run = kubectl,
        clock: Callable[[], float] | None = None,
    ) -> None:
        super().__init__(plan, run=run, revision=database.revision, idle=database.idle, clock=clock)
        self.database = database

    def check_original(self, deadline: float) -> None:
        super().check_original(deadline)
        if self.revision(self._remaining(deadline)) != ORIGINAL_REVISION:
            raise GuardError("schema expansion requires the original 0032 revision")

    def apply(self, deadline: float) -> None:
        try:
            self.check_original(deadline)
        except Exception:
            raise ReleaseNotStarted("schema expansion preflight rejected before writes") from None
        self._close(deadline)
        if not self.idle(self._remaining(deadline)):
            raise GuardError("business operations arrived while closing the schema window")
        self.database.expand(min(60, deadline - self.clock()))
        if self.revision(self._remaining(deadline)) != TARGET_REVISION:
            raise GuardError("schema expansion target was not verified")
        self._open(deadline)
        self.verify(False, deadline)
        self._clear_fences(deadline)
        validate_objects(self.plan.original, self._prerequisites(deadline))

    def restore(self, deadline: float) -> None:
        # The caller has acquired the same database session lock as the old
        # migration. Both complete schema states retain compatible original code.
        self._inspect(deadline)
        if not self.idle(self._remaining(deadline)):
            raise GuardError("business operations prevent schema-window restoration")
        self._close(deadline)
        if not self.idle(self._remaining(deadline)):
            raise GuardError("business operations arrived while closing the recovery entry")
        self._open(deadline)
        self.verify(False, deadline)
        self._clear_fences(deadline)
        validate_objects(self.plan.original, self._prerequisites(deadline))


@contextmanager
def locked_cluster(plan: ExpansionPlan, deadline: float) -> Iterator[ExpansionCluster]:
    # This read verifies the exact private Secret binding before opening libpq.
    # No cluster writes or schema operations occur until the lock is acquired.
    bootstrap = ReleaseCluster(plan)
    secret = bootstrap._secret(deadline)
    url = base64.b64decode(secret["data"]["DATABASE__URL"], validate=True).decode()
    remaining = min(10, deadline - host_clock().elapsed)
    with PsqlSession(url, connect_timeout=remaining) as database:
        yield ExpansionCluster(plan, database)


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
            raise GuardError("schema plan fingerprint changed")
        plan = ExpansionPlan(json.loads(raw))
        names = (*EXECUTOR_FILES, "presales_schema_expand.py")
        sources = plan.data.get("executor_sources", {})
        if set(sources) != set(names) or any(
            hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() != sources[name]
            for name in names
        ):
            raise GuardError("schema executor source bundle differs from its approved plan")
        if args.command == "validate":
            print(
                json.dumps(
                    {
                        "status": "valid",
                        "original_revision": ORIGINAL_REVISION,
                        "target_revision": TARGET_REVISION,
                        "migration": "fixed_additive",
                    }
                )
            )
            return
        if args.state is None:
            raise GuardError("schema state path is required")
        switch = Switch(args.state)
        target = Target(plan.operation, plan.executor, plan.namespace_uid, args.plan_sha256)
        if args.command == "status":
            result = switch.status()
            if result["target"] != asdict(target):
                raise GuardError("schema state and plan differ")
        elif args.command == "arm":
            deadline = host_clock().elapsed + 30
            with locked_cluster(plan, deadline) as cluster:
                cluster.check_original(deadline)
            switch.arm(target)
            result = switch.status()
        else:

            def apply(deadline: float) -> None:
                began = False
                try:
                    with locked_cluster(plan, deadline) as cluster:
                        began = True
                        cluster.apply(deadline)
                except Exception:
                    if not began:
                        raise ReleaseNotStarted("database lock unavailable before writes") from None
                    raise

            def restore(deadline: float) -> None:
                with locked_cluster(plan, deadline) as cluster:
                    cluster.restore(deadline)

            result = switch.execute(target, apply=apply, restore=restore)
        print(json.dumps(result, sort_keys=True))
        if result["phase"] in {"restored", "blocked"}:
            raise SystemExit(1)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        parser.exit(1, "schema expansion refused; inspect the private plan and state\n")


if __name__ == "__main__":
    main()
