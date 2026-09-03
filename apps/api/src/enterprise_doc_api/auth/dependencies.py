from __future__ import annotations

from typing import Annotated, Protocol, cast

from fastapi import Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from enterprise_doc_api.errors import ApiError
from enterprise_doc_core.context import PrincipalContext, enrich_request_principal


class PrincipalResolver(Protocol):
    async def resolve(self, token: str) -> PrincipalContext: ...


_BEARER_SCHEME = HTTPBearer(auto_error=False, scheme_name="BearerAuth")


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
    _credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Security(_BEARER_SCHEME),
    ],
) -> PrincipalContext:
    state_principal = getattr(request.state, "principal", None)
    if state_principal is not None:
        return cast(PrincipalContext, state_principal)
    return await resolve_request_principal(request)


async def resolve_request_principal(request: Request) -> PrincipalContext:
    authorization_values = request.headers.getlist("Authorization")
    if not authorization_values:
        raise MissingBearerToken()
    if len(authorization_values) != 1:
        raise MalformedBearerToken()
    return await resolve_bearer_principal(request, authorization_values[0])


async def resolve_bearer_principal(
    request: Request,
    authorization: str | None,
) -> PrincipalContext:
    if authorization is None:
        raise MissingBearerToken()
    scheme, separator, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or separator != " " or not token or " " in token:
        raise MalformedBearerToken()

    resolver = cast(PrincipalResolver, request.app.state.principal_resolver)
    principal = await resolver.resolve(token)
    request.state.auth_token = token
    enrich_request_principal(principal)
    return principal
