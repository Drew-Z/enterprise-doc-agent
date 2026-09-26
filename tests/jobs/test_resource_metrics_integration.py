from __future__ import annotations

import math
import socket
from datetime import timedelta
from functools import partial
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select, text, update
from sqlalchemy.engine import make_url
from tests.browser_sessions.conftest import browser_db as browser_db

from enterprise_doc_core.config import FoundationSettings
from enterprise_doc_core.identity import Tenant, User
from enterprise_doc_core.jobs import Job
from enterprise_doc_core.telemetry import MetricsRuntime
from enterprise_doc_core.telemetry.resources import (
    ResourceMetricsSampler,
    read_redis_connected_clients,
)

pytestmark = pytest.mark.integration


async def test_queue_observations_read_due_work_and_redis_connections_recover(browser_db):
    from enterprise_doc_core.jobs.metrics import read_queue_oldest_age

    settings = FoundationSettings()
    redis_url = settings.redis.url.get_secret_value()
    assert make_url(redis_url).host in {"127.0.0.1", "localhost", "::1"}
    redis = Redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)
    extra = Redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)
    tenant, actor = uuid4(), uuid4()
    metrics = MetricsRuntime.create()
    sampler = ResourceMetricsSampler(
        metrics,
        partial(read_queue_oldest_age, browser_db.sessions),
        partial(read_redis_connected_clients, redis),
    )
    try:
        assert await read_queue_oldest_age(browser_db.sessions) == 0
        async with browser_db.sessions.begin() as session:
            session.add(
                Tenant(
                    id=tenant,
                    name="resource telemetry",
                    slug="resource-" + tenant.hex,
                    quota_bytes=1024,
                )
            )
            session.add(User(id=actor, email=actor.hex + "@example.test"))
            await session.flush()
            now = await session.scalar(select(func.now()))
            specs = [
                ("pending", -20, "document.ingest"),
                ("retry_wait", -45, "agent.execute"),
                ("pending", -10, "presales.generate"),
                ("pending", 120, "document.ingest"),
                ("retry_wait", 120, "document.ingest"),
                ("running", -600, "agent.execute"),
                ("succeeded", -900, "document.ingest"),
                ("dead", -900, "document.ingest"),
                ("cancelled", -900, "document.ingest"),
            ]
            for status, delta, job_type in specs:
                session.add(
                    Job(
                        tenant_id=tenant,
                        actor_id=actor,
                        type=job_type,
                        status=status,
                        idempotency_key=uuid4().hex,
                        request_fingerprint="a" * 64,
                        payload={},
                        available_at=now + timedelta(seconds=delta),
                    )
                )
        async with browser_db.sessions() as session:
            before = list(
                (
                    await session.execute(select(Job.id, Job.status, Job.available_at, Job.version))
                ).all()
            )
        await sampler.sample_once()
        age = metrics.registry.get_sample_value("enterprise_doc_queue_oldest_age_seconds")
        assert 45 <= age < 60
        assert metrics.registry.get_sample_value("enterprise_doc_redis_connected_clients") >= 1
        async with browser_db.sessions() as session:
            after = list(
                (
                    await session.execute(select(Job.id, Job.status, Job.available_at, Job.version))
                ).all()
            )
        assert sorted(before) == sorted(after)
        async with browser_db.sessions.begin() as session:
            await session.execute(
                update(Job)
                .where(Job.status.in_(("pending", "retry_wait")), Job.available_at <= func.now())
                .values(status="succeeded")
            )
        await sampler.sample_once()
        assert metrics.registry.get_sample_value("enterprise_doc_queue_oldest_age_seconds") == 0
        clients = await read_redis_connected_clients(redis)
        await extra.ping()
        assert await read_redis_connected_clients(redis) >= clients + 1
        await extra.aclose()
        assert await read_redis_connected_clients(redis) == clients
        # Hold a loopback port without listening: fail an actual connection, no shared outage.
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            unavailable = Redis(
                host="127.0.0.1",
                port=reserved.getsockname()[1],
                socket_connect_timeout=0.1,
                socket_timeout=0.1,
            )
            try:
                failed_sampler = ResourceMetricsSampler(
                    metrics,
                    partial(read_queue_oldest_age, browser_db.sessions),
                    partial(read_redis_connected_clients, unavailable),
                    timeout_seconds=0.5,
                )
                await failed_sampler.sample_once()
                assert (
                    metrics.registry.get_sample_value("enterprise_doc_queue_oldest_age_seconds")
                    == 0
                )
                assert math.isnan(
                    metrics.registry.get_sample_value("enterprise_doc_redis_connected_clients")
                )
                assert (
                    metrics.registry.get_sample_value(
                        "enterprise_doc_resource_sample_success", {"source": "redis"}
                    )
                    == 0
                )
            finally:
                await unavailable.aclose()
        await sampler.sample_once()
        assert (
            metrics.registry.get_sample_value(
                "enterprise_doc_resource_sample_success", {"source": "redis"}
            )
            == 1
        )
        # Only this fixture's schema is locked. A blocked query must expire and
        # return its connection cleanly, while Redis continues to refresh.
        async with browser_db.engine.begin() as blocker:
            await blocker.execute(text("LOCK TABLE jobs IN ACCESS EXCLUSIVE MODE"))
            await sampler.sample_once()
            assert math.isnan(
                metrics.registry.get_sample_value("enterprise_doc_queue_oldest_age_seconds")
            )
            assert (
                metrics.registry.get_sample_value(
                    "enterprise_doc_resource_sample_success", {"source": "redis"}
                )
                == 1
            )
        await sampler.sample_once()
        assert metrics.registry.get_sample_value("enterprise_doc_queue_oldest_age_seconds") == 0
        from scripts.business_capacity_telemetry import parse_metrics

        parsed = parse_metrics(metrics.render().decode())
        assert parsed["gauges"]["enterprise_doc_queue_oldest_age_seconds"] == 0
        assert parsed["gauges"]["enterprise_doc_redis_connected_clients"] >= 1
        assert all(value["success"] == 1 for value in parsed["resource_samples"].values())
    finally:
        await extra.aclose()
        await redis.aclose()
