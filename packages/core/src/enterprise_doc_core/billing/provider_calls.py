from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_doc_core.billing.errors import UsageError
from enterprise_doc_core.billing.provider_metadata import provider_request_id, safe_provider_id
from enterprise_doc_core.billing.provider_models import ProviderDispatch
from enterprise_doc_core.config import ProviderUsageSettings

Guard = Callable[[AsyncSession], Awaitable[None]]
Kind = Literal["agent", "document", "query"]


@dataclass(frozen=True)
class _Scope:
    service: ProviderCallService
    tenant_id: UUID
    operation_id: UUID
    kind: Kind
    guard: Guard


_SCOPE: ContextVar[_Scope | None] = ContextVar("provider_usage_scope", default=None)


class ProviderCallService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        settings: ProviderUsageSettings | None = None,
    ) -> None:
        self.sessions = session_factory
        self.settings = settings or ProviderUsageSettings()

    @contextmanager
    def scope(
        self,
        *,
        tenant_id: UUID,
        operation_id: UUID,
        kind: Kind,
        guard: Guard,
    ) -> Iterator[None]:
        token = _SCOPE.set(_Scope(self, tenant_id, operation_id, kind, guard))
        try:
            yield
        finally:
            _SCOPE.reset(token)

    async def begin(self, scope: _Scope, *, provider: str, model: str, endpoint: str) -> UUID:
        now = datetime.now(UTC)
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        parsed = urlsplit(endpoint)
        # Do not persist URLs, credentials, payloads, headers or provider error messages.
        channel = f"{parsed.scheme}://{parsed.hostname}:{parsed.port or ''}{parsed.path}"
        try:
            async with self.sessions.begin() as session:
                await session.execute(text("SET LOCAL lock_timeout = '5s'"))
                await scope.guard(session)
                # Guards take business locks before budget locks. No path takes
                # a business/Tenant lock after acquiring a provider budget lock.
                for key in (
                    f"provider-day:{scope.tenant_id}:{day.date()}",
                    f"provider-operation:{scope.tenant_id}:{scope.kind}:{scope.operation_id}",
                ):
                    await session.execute(
                        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                        {"key": key},
                    )
                daily = await session.scalar(
                    select(func.count())
                    .select_from(ProviderDispatch)
                    .where(
                        ProviderDispatch.tenant_id == scope.tenant_id,
                        ProviderDispatch.started_at >= day,
                        ProviderDispatch.started_at < day + timedelta(days=1),
                    )
                )
                if (daily or 0) >= self.settings.daily_call_limit:
                    raise UsageError("provider_daily_budget_exhausted")
                attempts = await session.scalar(
                    select(func.count())
                    .select_from(ProviderDispatch)
                    .where(
                        ProviderDispatch.tenant_id == scope.tenant_id,
                        ProviderDispatch.kind == scope.kind,
                        ProviderDispatch.operation_id == scope.operation_id,
                    )
                )
                limit = {
                    "agent": self.settings.agent_call_limit,
                    "document": self.settings.document_call_limit,
                    "query": self.settings.query_call_limit,
                }[scope.kind]
                if (attempts or 0) >= limit:
                    raise UsageError("provider_operation_budget_exhausted")
                row = ProviderDispatch(
                    tenant_id=scope.tenant_id,
                    operation_id=scope.operation_id,
                    kind=scope.kind,
                    provider=provider[:80],
                    model=model[:200],
                    channel_hash=hashlib.sha256(channel.encode()).hexdigest(),
                    state="dispatched",
                    started_at=now,
                )
                session.add(row)
                await session.flush()
                receipt = row.id
            return receipt
        except DBAPIError as error:
            raise UsageError("provider_usage_unavailable") from error

    async def finish(
        self,
        scope: _Scope,
        receipt: UUID,
        *,
        state: str,
        response: httpx.Response | None = None,
    ) -> None:
        tokens = None
        response_id = None
        request_id = provider_request_id(response.headers) if response is not None else None
        if response is not None and len(response.content) <= 2 * 1024 * 1024:
            try:
                payload = response.json()
                response_id = safe_provider_id(payload.get("id"))
                candidate = payload.get("usage", {}).get("total_tokens")
                if type(candidate) is int and 0 <= candidate <= 2**31 - 1:
                    tokens = candidate
            except (ValueError, AttributeError, TypeError):
                pass
        try:
            async with self.sessions.begin() as session:
                row = await session.scalar(
                    select(ProviderDispatch)
                    .where(
                        ProviderDispatch.id == receipt,
                        ProviderDispatch.tenant_id == scope.tenant_id,
                        ProviderDispatch.operation_id == scope.operation_id,
                    )
                    .with_for_update()
                )
                if row is None:
                    raise UsageError("provider_usage_receipt_missing")
                if row.state != "dispatched":
                    return
                row.state = state
                row.status_code = response.status_code if response is not None else None
                row.provider_request_id = request_id
                row.provider_response_id = response_id
                row.total_tokens = tokens
                row.finished_at = datetime.now(UTC)
                # No published, versioned rate card is configured. Cost stays
                # NULL even when the provider returns token usage or an error.
        except DBAPIError as error:
            raise UsageError("provider_usage_unavailable") from error


async def recorded_post(
    client: httpx.AsyncClient,
    endpoint: str,
    *,
    provider: str,
    model: str,
    json_body: dict[str, Any],
    headers: dict[str, str],
    request_timeout: httpx.Timeout,
    require_metering: bool = False,
) -> httpx.Response:
    scope = _SCOPE.get()
    if scope is None:
        if require_metering:
            raise UsageError("provider_metering_context_missing")
        return await client.post(endpoint, json=json_body, headers=headers, timeout=request_timeout)
    receipt = await scope.service.begin(scope, provider=provider, model=model, endpoint=endpoint)
    try:
        response = await client.post(
            endpoint, json=json_body, headers=headers, timeout=request_timeout
        )
    except BaseException as error:
        state = (
            "cancelled"
            if isinstance(error, asyncio.CancelledError)
            else "timeout"
            if isinstance(error, httpx.TimeoutException)
            else "transport_error"
            if isinstance(error, httpx.RequestError)
            else "unknown"
        )
        try:
            async with asyncio.timeout(3):
                await scope.service.finish(scope, receipt, state=state)
        except (Exception, asyncio.CancelledError):
            # A committed 'dispatched' row is deliberately an unresolved cost,
            # not proof of success or a zero-cost request after a process crash.
            pass
        raise
    await scope.service.finish(
        scope,
        receipt,
        state="responded" if response.is_success else "http_error",
        response=response,
    )
    return response
