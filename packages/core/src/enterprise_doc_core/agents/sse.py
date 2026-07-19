from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from enterprise_doc_core.agents.events import (
    AgentPublicEventPayloadInvalid,
    public_event_payload,
)
from enterprise_doc_core.agents.service import AgentRunEventResult


class AgentSseCursorInvalid(ValueError):
    pass


class AgentSseEventInvalid(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AgentSseEnvelope:
    seq: int
    event_type: str
    event_version: int
    payload: dict[str, object]
    created_at: datetime


def parse_last_event_id(value: str | None) -> int:
    if value is None:
        return 0
    normalized = value.strip()
    if not normalized or not normalized.isascii() or not normalized.isdecimal():
        raise AgentSseCursorInvalid()
    cursor = int(normalized)
    if cursor < 0 or cursor > 9_223_372_036_854_775_807:
        raise AgentSseCursorInvalid()
    return cursor


def agent_sse_envelope(event: AgentRunEventResult) -> AgentSseEnvelope:
    if event.seq <= 0 or event.event_version <= 0:
        raise AgentSseEventInvalid()
    try:
        payload = public_event_payload(event.event_type, event.public_payload)
    except AgentPublicEventPayloadInvalid as error:
        raise AgentSseEventInvalid() from error
    return AgentSseEnvelope(
        seq=event.seq,
        event_type=event.event_type,
        event_version=event.event_version,
        payload=payload,
        created_at=event.created_at,
    )


def encode_agent_sse_event(event: AgentRunEventResult) -> str:
    envelope = agent_sse_envelope(event)
    data = json.dumps(
        {
            "createdAt": envelope.created_at.isoformat().replace("+00:00", "Z"),
            "eventType": envelope.event_type,
            "eventVersion": envelope.event_version,
            "payload": envelope.payload,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"id: {envelope.seq}\nevent: {envelope.event_type}\ndata: {data}\n\n"


def is_terminal_agent_event(event: AgentRunEventResult) -> bool:
    return event.event_type in {"run.cancelled", "run.finished"}


def agent_sse_heartbeat() -> str:
    return ": heartbeat\n\n"


__all__ = [
    "AgentSseCursorInvalid",
    "AgentSseEnvelope",
    "AgentSseEventInvalid",
    "agent_sse_envelope",
    "agent_sse_heartbeat",
    "encode_agent_sse_event",
    "is_terminal_agent_event",
    "parse_last_event_id",
]
