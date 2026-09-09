from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.engine import make_url
from tests.agent.test_agent_failure_diagnostics_integration import (
    UnavailableCheckpoint,
    UnreachableGateway,
    UnreachableMcpClient,
)
from tests.agent.test_agent_run_integration import _request, _seed_agent_context, _service

from enterprise_doc_core.agents import AgentRun
from enterprise_doc_core.config import (
    AppEnvironment,
    DatabaseSettings,
    EmbeddingSettings,
    McpSettings,
    ModelSettings,
    ObjectStoreSettings,
    RedisSettings,
)
from enterprise_doc_core.db import create_session_factory, ensure_asyncio_compatibility
from enterprise_doc_core.health import build_foundation_resources
from enterprise_doc_core.jobs import JobAttempt
from enterprise_doc_core.logging import configure_logging
from enterprise_doc_core.telemetry import MetricsRuntime
from enterprise_doc_worker.agents import build_durable_agent_handler
from enterprise_doc_worker.config import WorkerSettings
from enterprise_doc_worker.consumer_main import build_consumer_app
from enterprise_doc_worker.queue import JOB_TASK_NAME, AsyncTaskRunner, JobMessage

pytestmark = pytest.mark.integration


def _probe_registered_consumer() -> dict[str, Any]:
    """Use the production task adapter with explicit local state and no provider access."""
    ensure_asyncio_compatibility()
    settings = WorkerSettings(
        _env_file=None,
        app_env=AppEnvironment.LOCAL,
        database=DatabaseSettings(),
        redis=RedisSettings(),
        object_store=ObjectStoreSettings(),
        model=ModelSettings(),
        embedding=EmbeddingSettings(),
        mcp=McpSettings(),
    )
    target = make_url(settings.database.url.get_secret_value())
    assert target.host == "127.0.0.1" and target.database == "enterprise_doc"
    configure_logging(service="worker-consumer", environment="local", level="INFO")
    metrics = MetricsRuntime.create()
    resources = build_foundation_resources(settings, metrics=metrics)
    session_factory = create_session_factory(resources.database_engine)
    runner = AsyncTaskRunner(loop_factory=asyncio.SelectorEventLoop)
    gateway = UnreachableGateway()
    mcp_client = UnreachableMcpClient(command="unreachable-mcp")
    app = None
    try:
        # Creation, execution and readback share one loop and one connection pool.
        seeded = runner.run(_seed_agent_context(session_factory))
        service = _service(session_factory)
        created = runner.run(
            service.create(
                principal=seeded.principal,
                idempotency_key=f"consumer-attribution:{uuid4().hex}",
                request=_request(seeded, input_text="synthetic-private-question"),
            )
        )
        handler = build_durable_agent_handler(
            session_factory=session_factory,
            model_settings=settings.model,
            mcp_settings=settings.mcp,
            checkpointer=UnavailableCheckpoint(),
            gateway=gateway,
            mcp_client=mcp_client,
            metrics=metrics,
        )
        app, _, _ = build_consumer_app(
            settings,
            resources=resources,
            async_runner=runner,
            agent_handler=handler,
            metrics=metrics,
        )
        app.log.setup(loglevel="INFO", redirect_stdouts=True)
        message = JobMessage(
            job_id=created.job_id,
            tenant_id=seeded.tenant_id,
            event_id=uuid4(),
        )
        task = app.tasks[JOB_TASK_NAME]
        outcomes = [task.run(message.model_dump(mode="json")) for _ in range(2)]

        async def read_observation() -> dict[str, Any]:
            async with session_factory() as session:
                attempts = list(
                    (
                        await session.scalars(
                            select(JobAttempt).where(JobAttempt.job_id == created.job_id)
                        )
                    ).all()
                )
                run = await session.get(AgentRun, created.run_id)
            assert len(attempts) == 1 and run is not None
            attempt = attempts[0]
            assert attempt.worker_id not in metrics.render().decode()
            return {
                "worker_id": attempt.worker_id,
                "process_id": os.getpid(),
                "job_ref": hashlib.sha256(str(created.job_id).encode()).hexdigest(),
                "attempt_ref": hashlib.sha256(str(attempt.id).encode()).hexdigest(),
                "attempt_number": attempt.attempt_number,
                "attempt_status": attempt.status,
                "error_code": attempt.error_code,
                "error_type": attempt.error_class,
                "diagnostic_code": attempt.diagnostic_code,
                "retryable": attempt.retryable,
                "agent_status": run.status,
                "provider_request_count": run.provider_request_count,
                "gateway_calls": gateway.calls,
                "mcp_calls": mcp_client.calls,
                "outcomes": outcomes,
            }

        return runner.run(read_observation())
    finally:
        try:
            runner.run(resources.close())
        finally:
            runner.close()
            if app is not None:
                app.close()


def test_registered_consumer_correlates_process_attempt_and_safe_checkpoint_failure() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-B",
            "-c",
            (
                "import json, sys; "
                "from tests.agent.test_consumer_attempt_attribution_integration "
                "import _probe_registered_consumer; "
                "observation = _probe_registered_consumer(); "
                "sys.__stdout__.write(json.dumps(observation))"
            ),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    observation = json.loads(result.stdout)
    records = [json.loads(line) for line in result.stderr.splitlines()]
    configurations = [r for r in records if r["event"] == "worker_consumer_configured"]
    claims = [r for r in records if r["event"] == "job_attempt_claimed"]
    failures = [r for r in records if r["event"] == "job_attempt_failure_recorded"]
    assert len(configurations) == len(claims) == len(failures) == 1
    configuration, claim, failure = configurations[0], claims[0], failures[0]
    assert configuration["process_id"] == observation["process_id"]
    assert configuration["hostname"]
    assert configuration["worker_id"] == observation["worker_id"]
    assert observation["worker_id"].startswith("worker-local-")
    for record in (claim, failure):
        for key in ("worker_id", "job_ref", "attempt_ref", "attempt_number"):
            assert record[key] == observation[key]
        assert record["service"] == "worker-consumer"
    for key in ("error_code", "error_type", "diagnostic_code"):
        assert failure[key] == observation[key]
    assert failure["job_status"] == "dead"
    assert observation["attempt_status"] == "permanent_failed"
    assert observation["diagnostic_code"] == "agent.unexpected.database_operational_error"
    assert observation["retryable"] is False
    assert observation["agent_status"] == "failed"
    assert observation["attempt_number"] == 1
    assert observation["outcomes"] == ["failed", "duplicate_or_not_claimable"]
    assert observation["provider_request_count"] is None
    assert observation["gateway_calls"] == observation["mcp_calls"] == 0
    assert "synthetic-private" not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr
    assert "lease_token" not in result.stderr
    assert "fencing_token" not in result.stderr
    assert re.search(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", result.stderr) is None
