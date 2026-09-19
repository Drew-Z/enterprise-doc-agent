from __future__ import annotations

from collections.abc import Awaitable
from typing import Annotated, Literal, Protocol, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from enterprise_doc_api.auth import get_current_principal
from enterprise_doc_api.errors import ApiError
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.schemas import (
    CreatePacket,
    PacketSummary,
    PacketView,
    ReviewInput,
)


class PresalesServiceProtocol(Protocol):
    async def create(
        self, principal: PrincipalContext, payload: CreatePacket, key: str
    ) -> PacketView: ...
    async def list_packets(self, principal: PrincipalContext) -> list[PacketSummary]: ...
    async def get(self, principal: PrincipalContext, packet_id: UUID) -> PacketView: ...
    async def generate(
        self, principal: PrincipalContext, packet_id: UUID, row_id: UUID, key: str
    ) -> PacketView: ...
    async def review(
        self,
        principal: PrincipalContext,
        packet_id: UUID,
        row_id: UUID,
        payload: ReviewInput,
        key: str,
    ) -> PacketView: ...
    async def export(
        self, principal: PrincipalContext, packet_id: UUID, mode: Literal["draft", "reviewed"]
    ) -> bytes: ...


def no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def service(request: Request) -> PresalesServiceProtocol:
    return cast(PresalesServiceProtocol, request.app.state.presales_service)


def operation_key(key: Annotated[str | None, Header(alias="Idempotency-Key")] = None) -> str:
    if key is None:
        raise ApiError(
            status_code=400,
            code="presales_idempotency_key_required",
            message="请使用带有操作标识的请求。",
        )
    return key


Principal = Annotated[PrincipalContext, Depends(get_current_principal)]
Service = Annotated[PresalesServiceProtocol, Depends(service)]
Key = Annotated[str, Depends(operation_key)]


async def result[T](operation: Awaitable[T]) -> T:
    try:
        return await operation
    except PresalesError as error:
        code = error.code
        status = 409
        message = "当前操作无法完成。请刷新响应表后重试。"
        if code == "presales_forbidden":
            status, message = 403, "当前企业会话无权执行此操作。"
        elif code == "presales_entitlement_inactive":
            status, message = 403, "当前企业没有生效中的生成权益。请联系管理员。"
        elif code == "presales_usage_limit":
            status, message = 429, "当前企业本周期的可用生成额度不足。请稍后重试或联系管理员。"
        elif code == "presales_usage_unavailable":
            status, message = 503, "当前无法确认企业的生成额度。请稍后重试。"
        elif code in {"presales_not_found", "presales_source_unavailable"}:
            status, message = 404, "响应表或所选资料不可访问。请重新选择已就绪资料。"
        elif code == "presales_stale_sources":
            message = "资料版本或索引已改变。请使用当前资料新建响应表并重新复核。"
        elif code in {"presales_generation_disabled", "presales_model_not_configured"}:
            status, message = 503, "售前生成尚未启用或未配置模型。请联系管理员。"
        elif code in {"presales_daily_limit", "presales_attempt_limit", "presales_review_limit"}:
            status, message = 429, "已达到本次操作限额。请联系管理员。"
        elif code == "presales_generation_busy":
            message = "已有生成正在进行。请稍后刷新查看结果。"
        elif code == "presales_revision_conflict":
            message = "这条响应已被修改。请刷新后重新核对。"
        elif code == "presales_review_required":
            message = "请逐条完成复核。或选择导出带有未复核标识的草稿。"
        elif code == "presales_review_evidence_required":
            message = "此判断缺少相应证据或条件。请核对原文后再保存。"
        elif code == "presales_invalid_idempotency_key":
            status, message = 400, "操作标识无效。"
        raise ApiError(status_code=status, code=code, message=message) from error


router = APIRouter(prefix="/api/presales", tags=["presales"], dependencies=[Depends(no_store)])


@router.get("", response_model=list[PacketSummary])
async def list_packets(principal: Principal, svc: Service) -> list[PacketSummary]:
    return await result(svc.list_packets(principal))


@router.post("", response_model=PacketView, status_code=201)
async def create_packet(
    payload: CreatePacket, principal: Principal, svc: Service, key: Key
) -> PacketView:
    return await result(svc.create(principal, payload, key))


@router.get("/{packet_id}", response_model=PacketView)
async def get_packet(packet_id: UUID, principal: Principal, svc: Service) -> PacketView:
    return await result(svc.get(principal, packet_id))


@router.post("/{packet_id}/rows/{row_id}/generate", response_model=PacketView)
async def generate_row(
    packet_id: UUID, row_id: UUID, principal: Principal, svc: Service, key: Key
) -> PacketView:
    return await result(svc.generate(principal, packet_id, row_id, key))


@router.put("/{packet_id}/rows/{row_id}/review", response_model=PacketView)
async def review_row(
    packet_id: UUID,
    row_id: UUID,
    payload: ReviewInput,
    principal: Principal,
    svc: Service,
    key: Key,
) -> PacketView:
    return await result(svc.review(principal, packet_id, row_id, payload, key))


@router.get("/{packet_id}/export")
async def export_packet(
    packet_id: UUID,
    principal: Principal,
    svc: Service,
    mode: Annotated[Literal["draft", "reviewed"], Query()] = "draft",
) -> Response:
    content = await result(svc.export(principal, packet_id, mode))
    return Response(
        content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'attachment; filename="presales-responses.csv"',
            "X-Content-Type-Options": "nosniff",
        },
    )
