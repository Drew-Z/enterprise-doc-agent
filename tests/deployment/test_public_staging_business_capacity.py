import httpx
import pytest
from pydantic import ValidationError
from scripts.business_capacity import BusinessFailure, BusinessIO, BusinessPlan
from scripts.staging_business_capacity import PublicStagingBusinessPlan, StagingBusinessPlan
from tests.deployment.test_staging_business_capacity import payload


def public_payload():
    return payload() | {
        "base_url": "https://agent.example.com",
        "approved_api_origin": "https://agent.example.com",
    }


@pytest.mark.parametrize("plan_type", [BusinessPlan, StagingBusinessPlan])
def test_existing_entrypoints_still_reject_public_api(plan_type):
    value = public_payload()
    del value["approved_api_origin"]
    with pytest.raises(ValidationError):
        plan_type.model_validate(value)


@pytest.mark.parametrize(
    "origin",
    [
        "http://agent.example.com",
        "https://agent.example.com.evil.test",
        "https://user:secret@agent.example.com",
        "https://agent.example.com:444",
        "https://agent.example.com/api",
        "https://agent.example.com?token=secret",
        "https://agent.example.com#fragment",
        "https://agent.example.com\n",
        "http://127.0.0.1:8000",
    ],
)
def test_public_api_must_match_explicit_approved_https_origin(origin):
    with pytest.raises(ValidationError):
        PublicStagingBusinessPlan.model_validate(public_payload() | {"base_url": origin})


@pytest.mark.asyncio
async def test_real_client_uses_public_origin_and_keeps_object_client_unauthenticated():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    plan = PublicStagingBusinessPlan.model_validate(public_payload())
    io = BusinessIO(
        plan,
        "synthetic-token",
        api_transport=httpx.MockTransport(handler),
        object_transport=httpx.MockTransport(handler),
    )
    async with io.client() as client:
        await io.request(client, "GET", "/api/session")
    async with io.client(objects=True) as client:
        await io.request(client, "GET", plan.object_origins[0] + "/object")
    assert str(calls[0].url) == "https://agent.example.com/api/session"
    assert calls[0].headers["authorization"] == "Bearer synthetic-token"
    assert "authorization" not in calls[1].headers


@pytest.mark.asyncio
async def test_public_api_redirect_is_not_followed():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(307, headers={"location": "https://other.example.com/api/session"})

    plan = PublicStagingBusinessPlan.model_validate(public_payload())
    io = BusinessIO(plan, "synthetic-token", api_transport=httpx.MockTransport(handler))
    async with io.client() as client:
        response = await io.request(client, "GET", "/api/session", accepted=(307,))
    assert response.status_code == 307
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path", ["https://other.example.com/api/session", "https://user:secret@agent.example.com/"]
)
async def test_absolute_request_cannot_escape_authenticated_origin(path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={})

    plan = PublicStagingBusinessPlan.model_validate(public_payload())
    io = BusinessIO(plan, "synthetic-token", api_transport=httpx.MockTransport(handler))
    async with io.client() as client:
        with pytest.raises(BusinessFailure, match="api_origin_mismatch"):
            await io.request(client, "GET", path)
    assert calls == []
