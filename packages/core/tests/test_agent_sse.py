from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from enterprise_doc_core.agents import (
    AgentRunEventResult,
    AgentSseCursorInvalid,
    AgentSseEventInvalid,
    agent_sse_heartbeat,
    encode_agent_sse_event,
    is_terminal_agent_event,
    parse_last_event_id,
)


def _event(
    *, event_type: str = "run.started", payload: dict[str, object] | None = None
) -> AgentRunEventResult:
    return AgentRunEventResult(
        event_id=uuid4(),
        seq=2,
        event_type=event_type,
        event_version=1,
        public_payload=payload or {"status": "running"},
        created_at=datetime(2026, 7, 19, 1, 2, 3, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, 0), (" 12 ", 12), ("0", 0)],
)
def test_last_event_id_parser_is_strict_and_tenant_local(value: str | None, expected: int) -> None:
    assert parse_last_event_id(value) == expected


@pytest.mark.parametrize("value", ["-1", "+1", "1.0", "abc", "\uff11\uff12"])
def test_last_event_id_parser_rejects_non_decimal_cursors(value: str) -> None:
    with pytest.raises(AgentSseCursorInvalid):
        parse_last_event_id(value)


def test_sse_event_is_versioned_ordered_and_public_only() -> None:
    encoded = encode_agent_sse_event(_event())
    assert encoded.startswith("id: 2\nevent: run.started\n")
    assert '"eventVersion":1' in encoded
    assert '"createdAt":"2026-07-19T01:02:03Z"' in encoded
    assert "input_text" not in encoded
    assert is_terminal_agent_event(
        _event(event_type="run.finished", payload={"status": "succeeded"})
    )
    assert is_terminal_agent_event(
        _event(event_type="run.cancelled", payload={"status": "cancelled"})
    )


def test_sse_rejects_unknown_or_sensitive_public_payload() -> None:
    with pytest.raises(AgentSseEventInvalid):
        encode_agent_sse_event(_event(payload={"status": "running", "input_text": "do not expose"}))
    assert agent_sse_heartbeat() == ": heartbeat\n\n"
