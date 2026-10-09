import json
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select

from enterprise_doc_core.billing.models import UsageEvent, UsageReservation
from enterprise_doc_core.presales.background import BackgroundGeneration
from enterprise_doc_core.presales.gateway import OpenAICompatiblePresalesGateway
from enterprise_doc_core.presales.models import PresalesAttempt, PresalesProviderCall
from tests.browser_sessions.conftest import browser_db as browser_db
from tests.presales.test_presales_background_integration import background as background
from tests.presales.test_presales_background_integration import valid_response

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("durable", [False, True])
async def test_strict_request_survives_admission_execution_and_exactly_once_accounting(
    background, durable
):
    b = background
    b.settings.primary_strict_output = True
    b.settings.background_generation_enabled = durable
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        response = valid_response(request).json()
        message = response["choices"][0]["message"]
        content = json.loads(message["content"])
        content["missingInformation"] = []
        message["content"] = json.dumps(content)
        return httpx.Response(200, json=response)

    gateway = OpenAICompatiblePresalesGateway(
        b.service.generation.gateway.source_settings,
        presales_settings=b.settings,
        transport=httpx.MockTransport(respond),
    )
    b.service.generation.gateway = gateway
    packet = await b.service.create(b.context.principal, b.payload, "strict-create")
    headers = {"Authorization": "Bearer owner", "Idempotency-Key": "strict-once"}
    url = f"/api/presales/{packet.id}/rows/{packet.rows[0].id}/generate"
    admitted = await b.api.post(url, headers=headers, json={"executionMode": "auto"})
    assert admitted.status_code == (202 if durable else 200), admitted.text
    frozen = admitted.json()["rows"][0]["attempts"][0]
    assert frozen["executionPolicy"]["routes"][0]["promptVersion"] == "presales.v18"
    if durable:
        assert requests == []
        # A new worker instance must reconstruct strict mode from its bound template.
        worker = BackgroundGeneration(b.service.generation, {"primary": gateway})
        assert await worker.run_once("strict-worker")
        assert not await worker.run_once("strict-worker-again")
    replay = await b.api.post(url, headers=headers, json={"executionMode": "auto"})
    assert replay.status_code in {200, 202}
    refreshed = await b.api.get(f"/api/presales/{packet.id}", headers=headers)
    row = refreshed.json()["rows"][0]
    assert row["state"] == "drafted" and row["draft"]["status"] == "supported"
    assert row["attempts"][0]["executionPolicy"] == frozen["executionPolicy"]
    assert len(requests) == 1 and requests[0]["response_format"]["type"] == "json_schema"
    operation_id = UUID(frozen["id"])
    async with b.sessions() as session:
        reservation = await session.scalar(
            select(UsageReservation).where(UsageReservation.operation_id == operation_id)
        )
        assert reservation.state == "consumed"
        events = (
            await session.scalars(select(UsageEvent).where(UsageEvent.operation_id == operation_id))
        ).all()
        assert len(events) == 1
        if durable:
            calls = (
                await session.scalars(
                    select(PresalesProviderCall).where(
                        PresalesProviderCall.operation_id == operation_id
                    )
                )
            ).all()
            assert len(calls) == 1 and calls[0].state == "succeeded"


@pytest.mark.parametrize("accepted_strict", [False, True])
async def test_output_mode_drift_refuses_dispatch_and_releases_reservation(
    background, accepted_strict
):
    b = background
    b.settings.primary_strict_output = accepted_strict
    b.service.generation.gateway = OpenAICompatiblePresalesGateway(
        b.service.generation.gateway.source_settings,
        presales_settings=b.settings,
        transport=b.client,
    )
    packet = await b.service.create(b.context.principal, b.payload, "strict-drift-create")
    headers = {"Authorization": "Bearer owner", "Idempotency-Key": "strict-drift-once"}
    admitted = await b.api.post(
        f"/api/presales/{packet.id}/rows/{packet.rows[0].id}/generate",
        headers=headers,
        json={"executionMode": "auto"},
    )
    assert admitted.status_code == 202, admitted.text
    operation_id = UUID(admitted.json()["rows"][0]["attempts"][0]["id"])
    changed = OpenAICompatiblePresalesGateway(
        b.service.generation.gateway.settings, strict_output=not accepted_strict, transport=b.client
    )
    worker = BackgroundGeneration(b.service.generation, {"primary": changed})
    assert await worker.run_once("strict-drift-worker")
    assert b.requests == []
    async with b.sessions() as session:
        operation = await session.get(PresalesAttempt, operation_id)
        assert operation.error_code == "presales_execution_policy_unavailable"
        assert operation.provider_request_count == 0
        reservation = await session.scalar(
            select(UsageReservation).where(UsageReservation.operation_id == operation_id)
        )
        assert reservation.state == "released"
        assert not (
            await session.scalars(
                select(PresalesProviderCall).where(
                    PresalesProviderCall.operation_id == operation_id
                )
            )
        ).all()
