from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime
from typing import Annotated, Literal, Protocol, cast
from urllib.parse import unquote
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response, status
from pydantic import Field, StringConstraints, ValidationError

from enterprise_doc_api.auth import ExternalIdentity, GroupRoleMapper
from enterprise_doc_api.config import AuthSettings
from enterprise_doc_api.errors import ApiError, ErrorResponse
from enterprise_doc_api.schemas import ApiModel
from enterprise_doc_core.context import get_request_context
from enterprise_doc_core.identity.scim_service import ScimProvisioningError
from enterprise_doc_core.identity.scim_types import ScimUserPage, ScimUserResult

SCIM_USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
SCIM_LIST_RESPONSE_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
SCIM_SERVICE_PROVIDER_CONFIG_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"
SCIM_RESOURCE_TYPE_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:ResourceType"
SCIM_SCHEMA_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Schema"
SCIM_BULK_REQUEST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:BulkRequest"
SCIM_BULK_RESPONSE_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:BulkResponse"
SCIM_ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"
SCIM_PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
SCIM_BULK_MAX_OPERATIONS = 50
SCIM_BULK_MAX_PAYLOAD_SIZE = 1_048_576
ScimSubject = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]
_SCIM_FILTER_PATTERN = re.compile(
    r'^\s*(userName|externalId)\s+eq\s+"([^"\r\n]{1,512})"'
    r'(?:\s+and\s+(userName|externalId)\s+eq\s+"([^"\r\n]{1,512})")?\s*$',
    re.IGNORECASE,
)
_SCIM_USER_PATH_PATTERN = re.compile(r"^/Users(?:/([^/?]+))?$", re.IGNORECASE)


class ScimProvisioningServiceProtocol(Protocol):
    async def sync_user(self, **kwargs: object) -> ScimUserResult | None: ...

    async def get_user(
        self,
        *,
        tenant_id: UUID,
        issuer: str,
        subject: str,
    ) -> ScimUserResult | None: ...

    async def list_users(
        self,
        *,
        tenant_id: UUID,
        issuer: str,
        start_index: int = 1,
        count: int = 100,
        user_name: str | None = None,
        external_id: str | None = None,
    ) -> ScimUserPage | None: ...


class ScimGroupRequest(ApiModel):
    value: str | None = Field(default=None, min_length=1, max_length=256)
    display: str | None = Field(default=None, min_length=1, max_length=256)


class ScimUserRequest(ApiModel):
    schemas: list[str] = Field(default_factory=list, max_length=8)
    user_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=3, max_length=320)
    ]
    active: bool = True
    external_id: ScimSubject | None = None
    groups: list[ScimGroupRequest] = Field(default_factory=list, max_length=100)


class ScimGroupResponse(ApiModel):
    value: str
    display: str


class ScimMetaResponse(ApiModel):
    resource_type: Literal["User"] = "User"
    last_modified: datetime | None = None


class ScimUserResponse(ApiModel):
    schemas: list[str]
    id: str
    external_id: str
    user_name: str
    active: bool
    groups: list[ScimGroupResponse]
    meta: ScimMetaResponse


class ScimListResponse(ApiModel):
    schemas: list[str]
    total_results: int
    start_index: int
    items_per_page: int
    resources: list[object]


class ScimFeatureSupportResponse(ApiModel):
    supported: bool


class ScimBulkSupportResponse(ApiModel):
    supported: bool
    max_operations: int
    max_payload_size: int


class ScimFilterSupportResponse(ApiModel):
    supported: bool
    max_results: int


class ScimAuthenticationSchemeResponse(ApiModel):
    type: str
    name: str
    description: str
    spec_uri: str | None = None
    documentation_uri: str | None = None


class ScimServiceProviderConfigResponse(ApiModel):
    schemas: list[str]
    documentation_uri: str | None = None
    patch: ScimFeatureSupportResponse
    bulk: ScimBulkSupportResponse
    filter: ScimFilterSupportResponse
    change_password: ScimFeatureSupportResponse
    sort: ScimFeatureSupportResponse
    etag: ScimFeatureSupportResponse
    authentication_schemes: list[ScimAuthenticationSchemeResponse]


class ScimResourceTypeResponse(ApiModel):
    id: str
    name: str
    endpoint: str
    description: str
    schema_: str = Field(alias="schema")
    schema_extensions: list[object]


class ScimResourceTypesResponse(ApiModel):
    schemas: list[str]
    total_results: int
    start_index: int
    items_per_page: int
    resources: list[ScimResourceTypeResponse]


class ScimSchemaAttributeResponse(ApiModel):
    name: str
    type: str
    multi_valued: bool
    description: str
    required: bool
    returned: str
    uniqueness: str


class ScimSchemaResponse(ApiModel):
    id: str
    name: str
    description: str
    attributes: list[ScimSchemaAttributeResponse]


class ScimSchemasResponse(ApiModel):
    schemas: list[str]
    total_results: int
    start_index: int
    items_per_page: int
    resources: list[ScimSchemaResponse]


class ScimUsersResponse(ApiModel):
    schemas: list[str]
    total_results: int
    start_index: int
    items_per_page: int
    resources: list[ScimUserResponse]


class ScimBulkOperationRequest(ApiModel):
    method: str = Field(min_length=1, max_length=16)
    path: str = Field(min_length=1, max_length=1024)
    bulk_id: str | None = Field(default=None, min_length=1, max_length=128)
    data: dict[str, object] | None = None


class ScimBulkRequest(ApiModel):
    schemas: list[str] = Field(default_factory=list, max_length=8)
    operations: list[ScimBulkOperationRequest] = Field(
        min_length=1,
        max_length=SCIM_BULK_MAX_OPERATIONS,
    )


class ScimBulkOperationResponse(ApiModel):
    method: str
    path: str
    bulk_id: str | None = None
    status: str
    response: dict[str, object] | None = None


class ScimBulkResponse(ApiModel):
    schemas: list[str]
    operations: list[ScimBulkOperationResponse]


class ScimPatchOperationRequest(ApiModel):
    op: str = Field(min_length=1, max_length=16)
    path: str = Field(min_length=1, max_length=128)
    value: object


class ScimPatchRequest(ApiModel):
    schemas: list[str] = Field(default_factory=list, max_length=8)
    operations: list[ScimPatchOperationRequest] = Field(min_length=1, max_length=8)


class ScimDisabled(ApiError):
    def __init__(self) -> None:
        super().__init__(
            status_code=404,
            code="scim_disabled",
            message="SCIM provisioning is not enabled.",
        )


class ScimAuthenticationInvalid(ApiError):
    def __init__(self) -> None:
        super().__init__(
            status_code=401,
            code="scim_auth_invalid",
            message="The SCIM bearer token is invalid.",
            headers={"WWW-Authenticate": "Bearer"},
        )


discovery_router = APIRouter(prefix="/scim/v2", tags=["scim"])
router = APIRouter(prefix="/scim/v2/tenants", tags=["scim"])
_LOGGER = logging.getLogger("enterprise_doc_api.auth")


@discovery_router.get(
    "/ServiceProviderConfig",
    response_model=ScimServiceProviderConfigResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_scim_service_provider_config(request: Request) -> ScimServiceProviderConfigResponse:
    _settings(request)
    return ScimServiceProviderConfigResponse(
        schemas=[SCIM_SERVICE_PROVIDER_CONFIG_SCHEMA],
        patch=ScimFeatureSupportResponse(supported=True),
        bulk=ScimBulkSupportResponse(
            supported=True,
            max_operations=SCIM_BULK_MAX_OPERATIONS,
            max_payload_size=SCIM_BULK_MAX_PAYLOAD_SIZE,
        ),
        filter=ScimFilterSupportResponse(supported=True, max_results=200),
        change_password=ScimFeatureSupportResponse(supported=False),
        sort=ScimFeatureSupportResponse(supported=False),
        etag=ScimFeatureSupportResponse(supported=False),
        authentication_schemes=[
            ScimAuthenticationSchemeResponse(
                type="oauthbearertoken",
                name="Tenant bearer token",
                description="A tenant-scoped bearer token provisioned by the platform operator.",
            )
        ],
    )


@discovery_router.get(
    "/ResourceTypes",
    response_model=ScimResourceTypesResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_scim_resource_types(request: Request) -> ScimResourceTypesResponse:
    _settings(request)
    return ScimResourceTypesResponse(
        schemas=[SCIM_LIST_RESPONSE_SCHEMA],
        total_results=1,
        start_index=1,
        items_per_page=1,
        resources=[
            ScimResourceTypeResponse(
                id="User",
                name="User",
                endpoint="/Users",
                description="A tenant member provisioned from an external identity provider.",
                schema_=SCIM_USER_SCHEMA,
                schema_extensions=[],
            )
        ],
    )


@discovery_router.get(
    "/Schemas",
    response_model=ScimSchemasResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_scim_schemas(request: Request) -> ScimSchemasResponse:
    _settings(request)
    return ScimSchemasResponse(
        schemas=[SCIM_LIST_RESPONSE_SCHEMA],
        total_results=1,
        start_index=1,
        items_per_page=1,
        resources=[
            ScimSchemaResponse(
                id=SCIM_USER_SCHEMA,
                name="User",
                description="The constrained SCIM user projection supported by this deployment.",
                attributes=[
                    ScimSchemaAttributeResponse(
                        name="userName",
                        type="string",
                        multi_valued=False,
                        description="The member email address.",
                        required=True,
                        returned="default",
                        uniqueness="server",
                    ),
                    ScimSchemaAttributeResponse(
                        name="externalId",
                        type="string",
                        multi_valued=False,
                        description="The issuer-scoped external subject.",
                        required=False,
                        returned="default",
                        uniqueness="server",
                    ),
                    ScimSchemaAttributeResponse(
                        name="active",
                        type="boolean",
                        multi_valued=False,
                        description=(
                            "Whether the tenant membership and identity binding are active."
                        ),
                        required=False,
                        returned="default",
                        uniqueness="none",
                    ),
                    ScimSchemaAttributeResponse(
                        name="groups",
                        type="complex",
                        multi_valued=True,
                        description="Configured application role groups accepted on upsert.",
                        required=False,
                        returned="request",
                        uniqueness="none",
                    ),
                ],
            )
        ],
    )


@router.get(
    "/{tenant_id}/Users/{subject}",
    response_model=ScimUserResponse,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def get_scim_user(
    tenant_id: UUID,
    subject: ScimSubject,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> ScimUserResponse:
    settings = _settings(request)
    _authenticate(
        settings,
        tenant_id=tenant_id,
        authorization=authorization,
        method=request.method,
    )
    issuer = settings.scim_issuer
    assert issuer is not None
    service = _service(request)
    try:
        result = await service.get_user(
            tenant_id=tenant_id,
            issuer=issuer,
            subject=subject.strip(),
        )
    except ScimProvisioningError as error:
        raise _api_error(error) from error
    if result is None:
        raise ApiError(
            status_code=404,
            code="scim_provisioning_not_found",
            message="The SCIM resource was not found.",
        )
    return _response(result, groups=())


@router.get(
    "/{tenant_id}/Users",
    response_model=ScimUsersResponse,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def list_scim_users(
    tenant_id: UUID,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    start_index: Annotated[int, Query(alias="startIndex", ge=1, le=1_000_000)] = 1,
    count: Annotated[int, Query(ge=0, le=200)] = 100,
    filter_expression: Annotated[str | None, Query(alias="filter", max_length=512)] = None,
) -> ScimUsersResponse:
    settings = _settings(request)
    _authenticate(
        settings,
        tenant_id=tenant_id,
        authorization=authorization,
        method=request.method,
    )
    user_name_filter, external_id_filter = _parse_filter(filter_expression)
    issuer = settings.scim_issuer
    assert issuer is not None
    service = _service(request)
    try:
        page = await service.list_users(
            tenant_id=tenant_id,
            issuer=issuer,
            start_index=start_index,
            count=count,
            user_name=user_name_filter,
            external_id=external_id_filter,
        )
    except ScimProvisioningError as error:
        raise _api_error(error) from error
    if page is None:
        raise ApiError(
            status_code=404,
            code="scim_provisioning_not_found",
            message="The SCIM tenant was not found.",
        )
    return _users_response(page)


@router.patch(
    "/{tenant_id}/Users/{subject}",
    response_model=ScimUserResponse,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def patch_scim_user(
    tenant_id: UUID,
    subject: ScimSubject,
    payload: ScimPatchRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> ScimUserResponse:
    settings = _settings(request)
    _authenticate(
        settings,
        tenant_id=tenant_id,
        authorization=authorization,
        method=request.method,
    )
    if payload.schemas and SCIM_PATCH_SCHEMA not in payload.schemas:
        raise ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="scim_schema_unsupported",
            message="The SCIM patch schema is unsupported.",
        )
    issuer = settings.scim_issuer
    assert issuer is not None
    service = _service(request)
    normalized_subject = subject.strip()
    try:
        current = await service.get_user(
            tenant_id=tenant_id,
            issuer=issuer,
            subject=normalized_subject,
        )
    except ScimProvisioningError as error:
        raise _api_error(error) from error
    if current is None:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="scim_provisioning_not_found",
            message="The SCIM resource was not found.",
        )

    next_email = current.email
    next_active = current.is_active
    for operation in payload.operations:
        if operation.op.strip().casefold() != "replace":
            raise ApiError(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="scim_patch_operation_unsupported",
                message="Only replace operations are supported by this SCIM deployment.",
            )
        path = operation.path.strip().casefold()
        if path == "active":
            if not isinstance(operation.value, bool):
                raise ApiError(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    code="scim_patch_value_invalid",
                    message="The active patch value must be boolean.",
                )
            next_active = operation.value
        elif path == "username":
            if not isinstance(operation.value, str):
                raise ApiError(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    code="scim_patch_value_invalid",
                    message="The userName patch value must be a string.",
                )
            next_email = operation.value
        else:
            raise ApiError(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="scim_patch_path_unsupported",
                message="Only active and userName patch paths are supported.",
            )

    if not next_active and next_email != current.email:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="scim_patch_inactive_conflict",
            message="An inactive SCIM user cannot change userName in the same patch.",
        )
    context = get_request_context()
    try:
        result = await service.sync_user(
            tenant_id=tenant_id,
            issuer=issuer,
            subject=normalized_subject,
            email=next_email if next_active else None,
            role=current.role if next_active else None,
            is_active=next_active,
            request_id=context.request_id if context else None,
            correlation_id=context.correlation_id if context else None,
        )
    except ScimProvisioningError as error:
        raise _api_error(error) from error
    if result is None:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="scim_provisioning_not_found",
            message="The SCIM resource was not found.",
        )
    return _response(result, groups=())


@router.post(
    "/{tenant_id}/Bulk",
    response_model=ScimBulkResponse,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
    },
)
async def process_scim_bulk(
    tenant_id: UUID,
    payload: ScimBulkRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    content_length: Annotated[int | None, Header(alias="Content-Length")] = None,
) -> ScimBulkResponse:
    settings = _settings(request)
    _authenticate(
        settings,
        tenant_id=tenant_id,
        authorization=authorization,
        method=request.method,
    )
    if content_length is not None and content_length > SCIM_BULK_MAX_PAYLOAD_SIZE:
        raise ApiError(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            code="scim_bulk_payload_too_large",
            message="The SCIM bulk payload exceeds the configured size limit.",
        )
    if payload.schemas and SCIM_BULK_REQUEST_SCHEMA not in payload.schemas:
        raise ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="scim_schema_unsupported",
            message="The SCIM bulk request schema is unsupported.",
        )

    operations: list[ScimBulkOperationResponse] = []
    for operation in payload.operations:
        try:
            operations.append(
                await _execute_scim_bulk_operation(
                    tenant_id=tenant_id,
                    authorization=authorization,
                    operation=operation,
                    request=request,
                )
            )
        except ApiError as error:
            operations.append(_bulk_error(operation, error.status_code, error.code, error.message))
        except ValidationError:
            operations.append(
                _bulk_error(
                    operation,
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    "scim_request_invalid",
                    "The SCIM operation data is invalid.",
                )
            )

    return ScimBulkResponse(
        schemas=[SCIM_BULK_RESPONSE_SCHEMA],
        operations=operations,
    )


@router.put(
    "/{tenant_id}/Users/{subject}",
    response_model=ScimUserResponse,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def upsert_scim_user(
    tenant_id: UUID,
    subject: ScimSubject,
    payload: ScimUserRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> ScimUserResponse:
    settings = _settings(request)
    _authenticate(
        settings,
        tenant_id=tenant_id,
        authorization=authorization,
        method=request.method,
    )
    issuer = settings.scim_issuer
    assert issuer is not None
    normalized_subject = subject.strip()
    if payload.external_id is not None and payload.external_id != normalized_subject:
        raise ApiError(
            status_code=422,
            code="scim_subject_mismatch",
            message="The SCIM externalId must match the resource subject.",
        )
    if payload.schemas and SCIM_USER_SCHEMA not in payload.schemas:
        raise ApiError(
            status_code=422,
            code="scim_schema_unsupported",
            message="The SCIM user schema is unsupported.",
        )
    groups = _group_names(payload.groups)
    role = GroupRoleMapper(
        owner_groups=frozenset(settings.external_owner_groups),
        member_groups=frozenset(settings.external_member_groups),
        role_claim_enabled=False,
    ).map_role(
        _identity_for_mapping(
            issuer=issuer,
            subject=normalized_subject,
            tenant_id=tenant_id,
            groups=groups,
        )
    )
    if payload.active and role is None:
        raise ApiError(
            status_code=422,
            code="scim_role_unmapped",
            message="The SCIM groups do not map to an application role.",
        )
    service = _service(request)
    context = get_request_context()
    try:
        result = await service.sync_user(
            tenant_id=tenant_id,
            issuer=issuer,
            subject=normalized_subject,
            email=payload.user_name,
            role=role,
            is_active=payload.active,
            request_id=context.request_id if context else None,
            correlation_id=context.correlation_id if context else None,
        )
    except ScimProvisioningError as error:
        raise _api_error(error) from error
    if result is None:
        raise ApiError(
            status_code=404,
            code="scim_provisioning_not_found",
            message="The SCIM user was not found.",
        )
    return _response(result, groups=groups)


@router.delete(
    "/{tenant_id}/Users/{subject}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def delete_scim_user(
    tenant_id: UUID,
    subject: ScimSubject,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    settings = _settings(request)
    _authenticate(
        settings,
        tenant_id=tenant_id,
        authorization=authorization,
        method=request.method,
    )
    issuer = settings.scim_issuer
    assert issuer is not None
    service = _service(request)
    context = get_request_context()
    try:
        await service.sync_user(
            tenant_id=tenant_id,
            issuer=issuer,
            subject=subject.strip(),
            email=None,
            role=None,
            is_active=False,
            request_id=context.request_id if context else None,
            correlation_id=context.correlation_id if context else None,
        )
    except ScimProvisioningError as error:
        raise _api_error(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _settings(request: Request) -> AuthSettings:
    settings = cast(AuthSettings | None, getattr(request.app.state, "auth_settings", None))
    if settings is None or not settings.scim_enabled:
        raise ScimDisabled()
    return settings


def _service(request: Request) -> ScimProvisioningServiceProtocol:
    service = cast(
        ScimProvisioningServiceProtocol | None,
        getattr(request.app.state, "scim_provisioning_service", None),
    )
    if service is None:
        raise ApiError(
            status_code=503,
            code="scim_provisioning_unavailable",
            message="SCIM provisioning is unavailable.",
        )
    return service


def _authenticate(
    settings: AuthSettings,
    *,
    tenant_id: UUID,
    authorization: str | None,
    method: str,
) -> None:
    if authorization is None:
        _log_scim_auth_failure(method=method)
        raise ScimAuthenticationInvalid()
    scheme, separator, token = authorization.partition(" ")
    expected = settings.scim_tenant_tokens.get(str(tenant_id))
    if (
        scheme.lower() != "bearer"
        or separator != " "
        or not token
        or " " in token
        or expected is None
        or not secrets.compare_digest(token, expected.get_secret_value())
    ):
        _log_scim_auth_failure(method=method)
        raise ScimAuthenticationInvalid()


def _log_scim_auth_failure(*, method: str) -> None:
    _LOGGER.warning(
        "auth_failed",
        extra={
            "event_data": {
                "method": method,
                "surface": "scim",
                "error_code": "scim_auth_invalid",
                "error_type": "ScimAuthenticationInvalid",
            }
        },
    )


def _group_names(groups: list[ScimGroupRequest]) -> tuple[str, ...]:
    names: list[str] = []
    for group in groups:
        name = (group.value or group.display or "").strip()
        if name:
            names.append(name)
    return tuple(names)


def _parse_filter(value: str | None) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    match = _SCIM_FILTER_PATTERN.fullmatch(value)
    if match is None:
        raise ApiError(
            status_code=400,
            code="scim_filter_unsupported",
            message="Only userName/externalId equality filters joined by AND are supported.",
        )
    values: dict[str, str] = {}
    captures = (
        (match.group(1), match.group(2)),
        (match.group(3), match.group(4)),
    )
    for attribute, filter_value in captures:
        if attribute is None or filter_value is None:
            continue
        normalized_attribute = attribute.casefold()
        if normalized_attribute in values:
            raise ApiError(
                status_code=400,
                code="scim_filter_unsupported",
                message="A SCIM equality filter may reference each supported attribute only once.",
            )
        values[normalized_attribute] = filter_value
    return values.get("username"), values.get("externalid")


def _identity_for_mapping(
    *, issuer: str, subject: str, tenant_id: UUID, groups: tuple[str, ...]
) -> ExternalIdentity:
    return ExternalIdentity(
        issuer=issuer,
        audience="scim",
        subject=subject,
        actor_id=subject,
        tenant_id=str(tenant_id),
        groups=groups,
    )


def _response(result: ScimUserResult, *, groups: tuple[str, ...]) -> ScimUserResponse:
    return ScimUserResponse(
        schemas=[SCIM_USER_SCHEMA],
        id=result.subject,
        external_id=result.subject,
        user_name=result.email,
        active=result.is_active,
        groups=[ScimGroupResponse(value=group, display=group) for group in groups],
        meta=ScimMetaResponse(last_modified=result.last_modified),
    )


def _users_response(page: ScimUserPage) -> ScimUsersResponse:
    return ScimUsersResponse(
        schemas=[SCIM_LIST_RESPONSE_SCHEMA],
        total_results=page.total_results,
        start_index=page.start_index,
        items_per_page=page.items_per_page,
        resources=[_response(resource, groups=()) for resource in page.resources],
    )


async def _execute_scim_bulk_operation(
    *,
    tenant_id: UUID,
    authorization: str | None,
    operation: ScimBulkOperationRequest,
    request: Request,
) -> ScimBulkOperationResponse:
    path = unquote(operation.path.strip())
    match = _SCIM_USER_PATH_PATTERN.fullmatch(path)
    if match is None:
        raise ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="scim_bulk_path_unsupported",
            message="Only /Users and /Users/{subject} paths are supported.",
        )

    method = operation.method.strip().upper()
    subject = match.group(1)
    if subject is not None:
        subject = subject.strip()
        if not subject or len(subject) > 512:
            raise ApiError(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="scim_subject_invalid",
                message="The SCIM resource subject is invalid.",
            )

    if method in {"POST", "PUT"}:
        if operation.data is None:
            raise ApiError(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="scim_bulk_data_required",
                message="The SCIM operation requires a User resource body.",
            )
        user_payload = ScimUserRequest.model_validate(operation.data)
        if method == "POST":
            if subject is not None or user_payload.external_id is None:
                raise ApiError(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    code="scim_bulk_create_subject_required",
                    message="POST /Users requires externalId in the User resource.",
                )
            subject = user_payload.external_id
        assert subject is not None
        result = await upsert_scim_user(
            tenant_id=tenant_id,
            subject=subject,
            payload=user_payload,
            request=request,
            authorization=authorization,
        )
        return ScimBulkOperationResponse(
            method=method,
            path=operation.path,
            bulk_id=operation.bulk_id,
            status=str(status.HTTP_200_OK),
            response=result.model_dump(mode="json", by_alias=True),
        )

    if method == "DELETE":
        if subject is None:
            raise ApiError(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="scim_bulk_subject_required",
                message="DELETE requires a /Users/{subject} path.",
            )
        if operation.data is not None:
            raise ApiError(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="scim_bulk_data_unsupported",
                message="DELETE operations must not include a resource body.",
            )
        await delete_scim_user(
            tenant_id=tenant_id,
            subject=subject,
            request=request,
            authorization=authorization,
        )
        return ScimBulkOperationResponse(
            method=method,
            path=operation.path,
            bulk_id=operation.bulk_id,
            status=str(status.HTTP_204_NO_CONTENT),
        )

    if method == "PATCH":
        if subject is None:
            raise ApiError(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="scim_bulk_subject_required",
                message="PATCH requires a /Users/{subject} path.",
            )
        if operation.data is None:
            raise ApiError(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="scim_bulk_data_required",
                message="The SCIM operation requires a PatchOp resource body.",
            )
        patch_payload = ScimPatchRequest.model_validate(operation.data)
        result = await patch_scim_user(
            tenant_id=tenant_id,
            subject=subject,
            payload=patch_payload,
            request=request,
            authorization=authorization,
        )
        return ScimBulkOperationResponse(
            method=method,
            path=operation.path,
            bulk_id=operation.bulk_id,
            status=str(status.HTTP_200_OK),
            response=result.model_dump(mode="json", by_alias=True),
        )

    raise ApiError(
        status_code=status.HTTP_400_BAD_REQUEST,
        code="scim_bulk_method_unsupported",
        message="Only POST, PUT, PATCH, and DELETE operations are supported.",
    )


def _bulk_error(
    operation: ScimBulkOperationRequest,
    status_code: int,
    code: str,
    message: str,
) -> ScimBulkOperationResponse:
    return ScimBulkOperationResponse(
        method=operation.method.strip().upper(),
        path=operation.path,
        bulk_id=operation.bulk_id,
        status=str(status_code),
        response={
            "schemas": [SCIM_ERROR_SCHEMA],
            "status": str(status_code),
            "scimType": code,
            "detail": message,
        },
    )


def _api_error(error: ScimProvisioningError) -> ApiError:
    if error.code == "scim_provisioning_not_found":
        return ApiError(
            status_code=404, code=error.code, message="The SCIM resource was not found."
        )
    if error.code == "scim_provisioning_conflict":
        return ApiError(
            status_code=409,
            code=error.code,
            message="The SCIM resource conflicts with current membership state.",
        )
    return ApiError(status_code=422, code=error.code, message="The SCIM resource is invalid.")
