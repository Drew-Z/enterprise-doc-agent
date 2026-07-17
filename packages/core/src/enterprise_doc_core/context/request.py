from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PrincipalContext:
    tenant_id: str
    actor_id: str
    role: str


@dataclass(frozen=True, slots=True)
class RequestContext:
    request_id: str
    correlation_id: str
    principal: PrincipalContext | None = None


_request_context: ContextVar[RequestContext | None] = ContextVar(
    "enterprise_doc_request_context",
    default=None,
)


def set_request_context(context: RequestContext) -> Token[RequestContext | None]:
    return _request_context.set(context)


def reset_request_context(token: Token[RequestContext | None]) -> None:
    _request_context.reset(token)


def get_request_context() -> RequestContext | None:
    return _request_context.get()


def enrich_request_principal(principal: PrincipalContext) -> None:
    context = get_request_context()
    if context is None:
        raise RuntimeError("request context is unavailable")
    _request_context.set(
        RequestContext(
            request_id=context.request_id,
            correlation_id=context.correlation_id,
            principal=principal,
        )
    )
