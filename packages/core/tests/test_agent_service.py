from __future__ import annotations

from uuid import uuid4

import pytest

from enterprise_doc_core.agents.events import AgentPublicEventPayloadInvalid, public_event_payload
from enterprise_doc_core.agents.models import AgentRunTaskType
from enterprise_doc_core.agents.service import CreateAgentRunInput, agent_run_fingerprint
from enterprise_doc_core.config import AgentSettings, ModelProvider, ModelSettings


def _request() -> CreateAgentRunInput:
    return CreateAgentRunInput(
        document_version_id=uuid4(),
        task_type=AgentRunTaskType.STRUCTURED_EXTRACTION,
        input_text="Extract payment terms",
        extraction_schema={"type": "object", "properties": {"amount": {"type": "string"}}},
        publish_requested=True,
    )


def test_agent_run_fingerprint_is_canonical_and_behavior_versioned() -> None:
    request = _request()
    reordered = CreateAgentRunInput(
        document_version_id=request.document_version_id,
        task_type=request.task_type,
        input_text=request.input_text,
        extraction_schema={"properties": {"amount": {"type": "string"}}, "type": "object"},
        publish_requested=request.publish_requested,
    )
    agent = AgentSettings()
    model = ModelSettings()

    assert agent_run_fingerprint(request=request, agent=agent, model=model) == (
        agent_run_fingerprint(request=reordered, agent=agent, model=model)
    )
    changed_model = model.model_copy(
        update={
            "provider": ModelProvider.OPENAI_COMPATIBLE,
            "base_url": "https://models.example.test/v1",
            "api_key": "secret",
            "model_name": "production-model",
        }
    )
    assert agent_run_fingerprint(request=request, agent=agent, model=model) != (
        agent_run_fingerprint(request=request, agent=agent, model=changed_model)
    )


def test_agent_settings_bump_only_the_prompt_behavior_version() -> None:
    settings = AgentSettings()

    assert settings.graph_version == "m4.v2"
    assert settings.prompt_version == "m4.v5"
    assert settings.tool_schema_version == "m4.v2"


def test_agent_run_fingerprint_normalizes_persisted_input_text() -> None:
    request = _request()
    padded = CreateAgentRunInput(
        document_version_id=request.document_version_id,
        task_type=request.task_type,
        input_text=f"  {request.input_text}  ",
        extraction_schema=request.extraction_schema,
        publish_requested=request.publish_requested,
    )

    assert agent_run_fingerprint(
        request=request,
        agent=AgentSettings(),
        model=ModelSettings(),
    ) == agent_run_fingerprint(
        request=padded,
        agent=AgentSettings(),
        model=ModelSettings(),
    )


def test_public_agent_events_allow_only_typed_redacted_fields() -> None:
    version_id = uuid4()

    assert public_event_payload(
        "run.created",
        {
            "task_type": "question_answer",
            "document_version_id": version_id,
            "publish_requested": False,
        },
    ) == {
        "task_type": "question_answer",
        "document_version_id": str(version_id),
        "publish_requested": False,
    }

    with pytest.raises(AgentPublicEventPayloadInvalid):
        public_event_payload(
            "run.created",
            {
                "task_type": "question_answer",
                "document_version_id": version_id,
                "publish_requested": False,
                "input_text": "must not enter SSE",
            },
        )
    with pytest.raises(AgentPublicEventPayloadInvalid):
        public_event_payload("unknown.event", {})
