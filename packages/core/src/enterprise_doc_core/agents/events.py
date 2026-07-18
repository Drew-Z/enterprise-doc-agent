from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError

from enterprise_doc_core.agents.models import AgentRunTaskType


class AgentPublicEventPayloadInvalid(ValueError):
    pass


class _PublicPayload(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")


class _RunCreatedPayload(_PublicPayload):
    task_type: AgentRunTaskType
    document_version_id: UUID
    publish_requested: bool


class _RunCancelRequestedPayload(_PublicPayload):
    status: Literal["running"]


class _RunCancelledPayload(_PublicPayload):
    status: Literal["cancelled"]


PUBLIC_EVENT_PAYLOAD_MODELS: dict[str, type[_PublicPayload]] = {
    "run.created": _RunCreatedPayload,
    "run.cancel_requested": _RunCancelRequestedPayload,
    "run.cancelled": _RunCancelledPayload,
}


def public_event_payload(event_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    model = PUBLIC_EVENT_PAYLOAD_MODELS.get(event_type)
    if model is None:
        raise AgentPublicEventPayloadInvalid(f"unsupported public Agent event: {event_type}")
    try:
        validated = model.model_validate(dict(payload))
    except ValidationError as error:
        raise AgentPublicEventPayloadInvalid("public Agent event payload is invalid") from error
    return validated.model_dump(mode="json")
