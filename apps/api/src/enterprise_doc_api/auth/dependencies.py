from __future__ import annotations

from typing import Annotated, Protocol, cast

from fastapi import Header, Request
from opentelemetry import trace

from enterprise_doc_api.errors import ApiError
from enterprise_doc_core.context import PrincipalContext, enrich_request_principal


class PrincipalResolver(Protocol):
    async def resolve(self, token: str) -> PrincipalContext: ...


class MissingBearerToken(ApiError):
    def __init__(self) -> None:
        super().__init__(
            status_code=401,
            code="auth_missing",
            message="A bearer token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )


class MalformedBearerToken(ApiError):
    def __init__(self) -> None:
        super().__init__(
            status_code=401,
            code="auth_invalid",
            message="The authorization header must contain one bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_principal(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> PrincipalContext:
    if authorization is None:
        raise MissingBearerToken()
    scheme, separator, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or separator != " " or not token or " " in token:
        raise MalformedBearerToken()

    resolver = cast(PrincipalResolver, request.app.state.principal_resolver)
    principal = await resolver.resolve(token)
    enrich_request_principal(principal)
    span = trace.get_current_span()
    span.set_attribute("app.tenant_id", principal.tenant_id)
    span.set_attribute("app.actor_id", principal.actor_id)
    return principal
