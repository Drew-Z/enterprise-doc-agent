import httpx
import pytest

from enterprise_doc_core.billing.provider_metadata import provider_request_id, safe_provider_id


@pytest.mark.parametrize(
    "value", [None, True, 12, "", "x" * 201, "id\nsecret", "https://secret", "=SUM(1)"]
)
def test_unusable_provider_ids_are_missing_not_truncated(value):
    assert safe_provider_id(value) is None


def test_provider_id_header_allowlist_and_precedence():
    assert safe_provider_id("chatcmpl-A_1.2") == "chatcmpl-A_1.2"
    assert (
        provider_request_id(httpx.Headers({"X-Request-ID": "req-1", "request-id": "req-2"}))
        == "req-1"
    )
    assert provider_request_id(httpx.Headers({"request-id": "req-2"})) == "req-2"
    assert (
        provider_request_id(httpx.Headers({"Authorization": "secret", "x-trace-id": "trace"}))
        is None
    )
    assert (
        provider_request_id(httpx.Headers({"x-request-id": "invalid value", "request-id": "req-2"}))
        == "req-2"
    )


@pytest.mark.parametrize("fault", ["http", "invalid_output", "body_timeout", "oversized"])
async def test_presales_errors_keep_header_id_after_response_started(fault):
    from enterprise_doc_core.config import ModelSettings
    from enterprise_doc_core.presales.errors import PresalesError
    from enterprise_doc_core.presales.gateway import OpenAICompatiblePresalesGateway
    from enterprise_doc_core.presales.schemas import GenerationInput, RequirementInput

    class InterruptedBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"{"
            raise httpx.ReadTimeout("private upstream diagnostic")

    def respond(request):
        headers = {"x-request-id": "req-after-headers"}
        if fault == "http":
            return httpx.Response(503, headers=headers)
        if fault == "body_timeout":
            return httpx.Response(200, headers=headers, stream=InterruptedBody())
        if fault == "oversized":
            return httpx.Response(200, headers=headers, content=b"x" * 1048577)
        return httpx.Response(200, headers=headers, json={"id": "resp-invalid", "choices": []})

    gateway = OpenAICompatiblePresalesGateway(
        ModelSettings(
            provider="openai_compatible",
            base_url="https://synthetic.invalid/v1",
            api_key="test-only",
            model_name="test",
        ),
        transport=httpx.MockTransport(respond),
    )
    with pytest.raises(PresalesError) as caught:
        await gateway.generate(
            GenerationInput(
                requirement=RequirementInput(key="R1", text="Test"), sources=[], evidence=[]
            )
        )
    assert caught.value.provider_request_id == "req-after-headers"
    assert caught.value.provider_response_id == (
        "resp-invalid" if fault == "invalid_output" else None
    )
    assert "private upstream diagnostic" not in str(caught.value)
