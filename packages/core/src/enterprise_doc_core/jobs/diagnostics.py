from __future__ import annotations

from typing import TypeGuard

GROUNDING_DIAGNOSTIC_CODES = frozenset(
    {
        "grounding.citation_chunk_not_in_candidates",
        "grounding.citation_excerpt_empty",
        "grounding.citation_excerpt_not_verbatim",
        "grounding.citation_excerpt_too_long",
        "grounding.citation_not_authorized",
        "grounding.citation_wrong_version",
    }
)

MCP_TOOL_NAMES = frozenset(
    {
        "create_draft_artifact",
        "get_artifact",
        "publish_artifact",
        "read_chunk",
        "search_document",
    }
)

MCP_DIAGNOSTIC_SUBCODES = frozenset(
    {
        "execution_context_expired",
        "execution_context_invalid_signature",
        "execution_context_malformed",
        "execution_context_missing",
        "execution_context_not_yet_valid",
        "execution_context_version_unsupported",
        "mcp_tool_failed",
        "mcp_tool_timeout",
        "returned_error",
        "tool_approval_denied",
        "tool_artifact_integrity_error",
        "tool_capability_denied",
        "tool_execution_error",
        "tool_execution_in_progress",
        "tool_idempotency_conflict",
        "tool_input_invalid",
        "tool_object_store_unavailable",
        "tool_policy_denied",
        "tool_prior_failure",
        "tool_result_invalid",
    }
)


def is_allowed_job_diagnostic_code(value: object) -> TypeGuard[str]:
    if not isinstance(value, str):
        return False
    if value in GROUNDING_DIAGNOSTIC_CODES:
        return True
    parts = value.split(".")
    return (
        len(parts) == 3
        and parts[0] == "mcp"
        and parts[1] in MCP_TOOL_NAMES
        and parts[2] in MCP_DIAGNOSTIC_SUBCODES
    )


def mcp_diagnostic_code(*, tool_name: str, subcode: str | None) -> str | None:
    if tool_name not in MCP_TOOL_NAMES:
        return None
    safe_subcode = subcode if subcode in MCP_DIAGNOSTIC_SUBCODES else "returned_error"
    return f"mcp.{tool_name}.{safe_subcode}"


__all__ = [
    "GROUNDING_DIAGNOSTIC_CODES",
    "MCP_DIAGNOSTIC_SUBCODES",
    "MCP_TOOL_NAMES",
    "is_allowed_job_diagnostic_code",
    "mcp_diagnostic_code",
]
