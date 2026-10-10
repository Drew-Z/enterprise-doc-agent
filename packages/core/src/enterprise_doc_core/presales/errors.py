from __future__ import annotations

from enum import StrEnum


class OutputDiagnostic(StrEnum):
    ENVELOPE_JSON = "envelope_json"
    ENVELOPE_SHAPE = "envelope_shape"
    INCOMPLETE_OUTPUT = "incomplete_output"
    UNSAFE_RESPONSE = "unsafe_response"
    DRAFT_JSON = "draft_json"
    DRAFT_SCHEMA = "draft_schema"
    BASIS_QUOTE = "basis_quote"
    SUPPORT_QUOTE = "support_quote"
    SUPPORT_COMBINATION = "support_combination"
    DRAFT_CONTRACT = "draft_contract"
    CITATION = "citation"
    STREAM_CONTRACT = "stream_contract"


class OutputContractError(ValueError):
    def __init__(self, diagnostic: OutputDiagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(
            {
                OutputDiagnostic.BASIS_QUOTE: "unsupported literal prerequisite basis",
                OutputDiagnostic.SUPPORT_QUOTE: "unsupported literal quotation",
            }.get(diagnostic, diagnostic.value)
        )


class PresalesError(Exception):
    """Public code only; never expose provider errors or source content."""

    def __init__(
        self,
        code: str,
        *,
        provider_requests: int = 0,
        retryable: bool = False,
        usage: dict[str, int | None] | None = None,
        provider_response_id: str | None = None,
        provider_request_id: str | None = None,
        diagnostic: OutputDiagnostic | None = None,
    ) -> None:
        self.code = code
        self.provider_requests = provider_requests
        self.retryable = retryable
        self.usage = usage
        self.provider_response_id = provider_response_id
        self.provider_request_id = provider_request_id
        self.diagnostic_code = (
            OutputDiagnostic(diagnostic).value if diagnostic is not None else None
        )
        super().__init__(code)
