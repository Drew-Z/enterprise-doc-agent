from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import TracebackType
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import delete, func, select

from enterprise_doc_api.app import create_app
from enterprise_doc_api.config import ApiSettings
from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.db import create_database_engine, create_session_factory
from enterprise_doc_core.identity import Membership, MembershipRole, Tenant, User
from enterprise_doc_core.object_store import ObjectStoreUnavailable
from enterprise_doc_core.uploads import (
    UploadInitializationInProgress,
    UploadSession,
    UploadSessionStatus,
)
from enterprise_doc_core.uploads.service import (
    CreateUploadSessionInput,
    UploadCreationService,
    UploadInitializationFailed,
)


@dataclass(frozen=True, slots=True)
class FixturePrincipal:
    tenant_id: UUID
    actor_id: UUID
    membership_id: UUID
    token: str
    quota_bytes: int

    @property
    def context(self) -> PrincipalContext:
        return PrincipalContext(
            tenant_id=str(self.tenant_id),
            actor_id=str(self.actor_id),
            role=MembershipRole.OWNER.value,
        )


class TokenPrincipalResolver:
    def __init__(self, principals: list[FixturePrincipal]) -> None:
        self._principals = {principal.token: principal.context for principal in principals}

    async def resolve(self, token: str) -> PrincipalContext:
        return self._principals[token]


class CountingObjectStore:
    def __init__(self) -> None:
        self.created: list[tuple[str, str, dict[str, str]]] = []
        self.aborted: list[tuple[str, str, str]] = []
        self.error: Exception | None = None
        self.abort_error: Exception | None = None

    async def create_upload(
        self,
        *,
        bucket: str,
        key: str,
        metadata: dict[str, str],
    ) -> str:
        if self.error is not None:
            raise self.error
        self.created.append((bucket, key, metadata))
        return f"upload-{len(self.created)}"

    async def abort_upload(
        self,
        *,
        bucket: str,
        key: str,
        upload_id: str,
    ) -> None:
        self.aborted.append((bucket, key, upload_id))
        if self.abort_error is not None:
            raise self.abort_error


class BlockingObjectStore(CountingObjectStore):
    def __init__(self) -> None:
        super().__init__()
        self.create_started = asyncio.Event()
        self.release_create = asyncio.Event()

    async def create_upload(
        self,
        *,
        bucket: str,
        key: str,
        metadata: dict[str, str],
    ) -> str:
        self.create_started.set()
        await self.release_create.wait()
        return await super().create_upload(bucket=bucket, key=key, metadata=metadata)


class CommitOutcomeUnknownTransaction:
    def __init__(
        self,
        inner: Any,
        *,
        fail_after_commit: bool,
        fail_before_commit: bool,
    ) -> None:
        self.inner = inner
        self.fail_after_commit = fail_after_commit
        self.fail_before_commit = fail_before_commit

    async def __aenter__(self) -> Any:
        return await self.inner.__aenter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        if self.fail_before_commit and exc_type is None:
            error = ConnectionError("commit failed before persistence")
            await self.inner.__aexit__(type(error), error, error.__traceback__)
            raise error
        result = await self.inner.__aexit__(exc_type, exc_value, traceback)
        if self.fail_after_commit and exc_type is None:
            raise ConnectionError("commit acknowledgement lost")
        return result


class FailingSessionRead:
    async def __aenter__(self) -> Any:
        raise ConnectionError("activation outcome read failed")

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class CommitOutcomeUnknownSessionFactory:
    def __init__(
        self,
        inner: Any,
        *,
        fail_on_begin: int | None = None,
        fail_before_commit_on_begin: int | None = None,
        fail_on_call: int | None = None,
    ) -> None:
        self.inner = inner
        self.fail_on_begin = fail_on_begin
        self.fail_before_commit_on_begin = fail_before_commit_on_begin
        self.fail_on_call = fail_on_call
        self.begin_calls = 0
        self.call_calls = 0

    def __call__(self) -> Any:
        self.call_calls += 1
        if self.call_calls == self.fail_on_call:
            return FailingSessionRead()
        return self.inner()

    def begin(self) -> CommitOutcomeUnknownTransaction:
        self.begin_calls += 1
        return CommitOutcomeUnknownTransaction(
            self.inner.begin(),
            fail_after_commit=self.begin_calls == self.fail_on_begin,
            fail_before_commit=self.begin_calls == self.fail_before_commit_on_begin,
        )


def _principal(*, label: str, quota_bytes: int) -> FixturePrincipal:
    return FixturePrincipal(
        tenant_id=uuid4(),
        actor_id=uuid4(),
        membership_id=uuid4(),
        token=f"token-{label}",
        quota_bytes=quota_bytes,
    )


def _headers(principal: FixturePrincipal, key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {principal.token}",
        "Idempotency-Key": key,
    }


def _body(*, filename: str, size_bytes: int) -> dict[str, object]:
    return {
        "filename": filename,
        "sizeBytes": size_bytes,
        "mediaType": "text/plain",
        "sha256": "a" * 64,
    }


async def _post(
    client: AsyncClient,
    principal: FixturePrincipal,
    key: str,
    *,
    filename: str,
    size_bytes: int,
) -> Response:
    return await client.post(
        "/api/upload-sessions",
        headers=_headers(principal, key),
        json=_body(filename=filename, size_bytes=size_bytes),
    )


@pytest.mark.integration
async def test_concurrent_create_is_idempotent_and_quota_safe() -> None:
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    session_factory = create_session_factory(engine)
    same = _principal(label="same", quota_bytes=1_000)
    conflict = _principal(label="conflict", quota_bytes=1_000)
    quota = _principal(label="quota", quota_bytes=100)
    revoked = _principal(label="revoked", quota_bytes=1_000)
    principals = [same, conflict, quota, revoked]
    object_store = CountingObjectStore()

    try:
        async with session_factory.begin() as session:
            for item in principals:
                session.add(
                    Tenant(
                        id=item.tenant_id,
                        name=f"Upload Integration {item.token}",
                        slug=f"upload-{item.tenant_id}",
                        quota_bytes=item.quota_bytes,
                    )
                )
                session.add(User(id=item.actor_id, email=f"{item.actor_id}@example.test"))
            await session.flush()
            for item in principals:
                session.add(
                    Membership(
                        id=item.membership_id,
                        tenant_id=item.tenant_id,
                        user_id=item.actor_id,
                        role=MembershipRole.OWNER.value,
                    )
                )
        async with session_factory.begin() as session:
            revoked_membership = await session.get(Membership, revoked.membership_id)
            assert revoked_membership is not None
            revoked_membership.is_active = False

        app = create_app(
            settings=settings,
            checkers=[],
            principal_resolver=TokenPrincipalResolver(principals),
            upload_creation_service=UploadCreationService(
                session_factory=session_factory,
                settings=settings.upload,
                object_store=object_store,
                documents_bucket=settings.object_store.documents_bucket,
            ),
        )
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            same_results = await asyncio.gather(
                _post(client, same, "same-key", filename="same.txt", size_bytes=64),
                _post(client, same, "same-key", filename="same.txt", size_bytes=64),
            )
            conflict_results = await asyncio.gather(
                _post(
                    client,
                    conflict,
                    "conflict-key",
                    filename="first.txt",
                    size_bytes=64,
                ),
                _post(
                    client,
                    conflict,
                    "conflict-key",
                    filename="second.txt",
                    size_bytes=65,
                ),
            )
            quota_results = await asyncio.gather(
                _post(client, quota, "quota-a", filename="a.txt", size_bytes=60),
                _post(client, quota, "quota-b", filename="b.txt", size_bytes=60),
            )
            revoked_result = await _post(
                client,
                revoked,
                "revoked-key",
                filename="revoked.txt",
                size_bytes=60,
            )

        assert sorted(response.status_code for response in same_results) == [200, 201]
        assert len({response.json()["sessionId"] for response in same_results}) == 1
        assert {response.json()["status"] for response in same_results} == {"active"}
        assert sorted(response.json()["replayed"] for response in same_results) == [False, True]
        assert sorted(response.status_code for response in conflict_results) == [201, 409]
        assert sorted(response.status_code for response in quota_results) == [201, 409]
        assert (
            next(
                response.json()["error"]["code"]
                for response in conflict_results
                if response.status_code == 409
            )
            == "upload_idempotency_conflict"
        )
        assert (
            next(
                response.json()["error"]["code"]
                for response in quota_results
                if response.status_code == 409
            )
            == "upload_quota_exceeded"
        )
        assert revoked_result.status_code == 404
        assert revoked_result.json()["error"]["code"] == "upload_tenant_unavailable"
        assert len(object_store.created) == 3
        assert all(
            item[0] == settings.object_store.documents_bucket for item in object_store.created
        )
        assert all("upload-session-id" in item[2] for item in object_store.created)
        assert all("filename" not in item[2] for item in object_store.created)

        async with session_factory() as session:
            for item, expected_count in (
                (same, 1),
                (conflict, 1),
                (quota, 1),
                (revoked, 0),
            ):
                count = await session.scalar(
                    select(func.count())
                    .select_from(UploadSession)
                    .where(UploadSession.tenant_id == item.tenant_id)
                )
                tenant = await session.get(Tenant, item.tenant_id)
                assert count == expected_count
                assert tenant is not None
                if expected_count:
                    rows = (
                        await session.scalars(
                            select(UploadSession).where(UploadSession.tenant_id == item.tenant_id)
                        )
                    ).all()
                    assert all(row.status == "active" for row in rows)
                    assert all(row.object_store_upload_id for row in rows)
            same_tenant = await session.get(Tenant, same.tenant_id)
            conflict_tenant = await session.get(Tenant, conflict.tenant_id)
            quota_tenant = await session.get(Tenant, quota.tenant_id)
            revoked_tenant = await session.get(Tenant, revoked.tenant_id)
            assert same_tenant is not None and same_tenant.reserved_storage_bytes == 64
            assert conflict_tenant is not None
            assert conflict_tenant.reserved_storage_bytes in {64, 65}
            assert quota_tenant is not None and quota_tenant.reserved_storage_bytes == 60
            assert revoked_tenant is not None and revoked_tenant.reserved_storage_bytes == 0
    finally:
        async with session_factory.begin() as session:
            tenant_ids = [item.tenant_id for item in principals]
            actor_ids = [item.actor_id for item in principals]
            membership_ids = [item.membership_id for item in principals]
            await session.execute(
                delete(UploadSession).where(UploadSession.tenant_id.in_(tenant_ids))
            )
            await session.execute(delete(Membership).where(Membership.id.in_(membership_ids)))
            await session.execute(delete(User).where(User.id.in_(actor_ids)))
            await session.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
        await engine.dispose()


@pytest.mark.integration
async def test_initializing_replay_waits_for_the_owner_to_activate() -> None:
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    session_factory = create_session_factory(engine)
    principal = _principal(label="initializing-replay", quota_bytes=1_000)
    object_store = BlockingObjectStore()
    service = UploadCreationService(
        session_factory=session_factory,
        settings=settings.upload,
        object_store=object_store,
        documents_bucket=settings.object_store.documents_bucket,
    )
    request = CreateUploadSessionInput(
        filename="replay.txt",
        size_bytes=64,
        media_type="text/plain",
        sha256="a" * 64,
    )
    first_task: asyncio.Task[object] | None = None
    second_task: asyncio.Task[object] | None = None

    try:
        async with session_factory.begin() as session:
            session.add(
                Tenant(
                    id=principal.tenant_id,
                    name="Initializing Replay",
                    slug=f"upload-{principal.tenant_id}",
                    quota_bytes=principal.quota_bytes,
                )
            )
            session.add(User(id=principal.actor_id, email=f"{principal.actor_id}@example.test"))
            await session.flush()
            session.add(
                Membership(
                    id=principal.membership_id,
                    tenant_id=principal.tenant_id,
                    user_id=principal.actor_id,
                    role=MembershipRole.OWNER.value,
                )
            )

        first_task = asyncio.create_task(
            service.create(
                principal=principal.context,
                idempotency_key="initializing-replay-key",
                request=request,
            )
        )
        await object_store.create_started.wait()
        timeout_service = UploadCreationService(
            session_factory=session_factory,
            settings=settings.upload,
            object_store=object_store,
            documents_bucket=settings.object_store.documents_bucket,
            initialization_wait_timeout_seconds=0.02,
            initialization_poll_interval_seconds=0.005,
        )
        with pytest.raises(UploadInitializationInProgress):
            await timeout_service.create(
                principal=principal.context,
                idempotency_key="initializing-replay-key",
                request=request,
            )
        second_task = asyncio.create_task(
            service.create(
                principal=principal.context,
                idempotency_key="initializing-replay-key",
                request=request,
            )
        )
        await asyncio.sleep(0.05)

        assert not second_task.done()

        object_store.release_create.set()
        first, second = await asyncio.gather(first_task, second_task)
        assert first.session_id == second.session_id
        assert first.status == second.status == "active"
        assert sorted((first.replayed, second.replayed)) == [False, True]
        assert len(object_store.created) == 1

        async with session_factory() as session:
            tenant = await session.get(Tenant, principal.tenant_id)
            assert tenant is not None and tenant.reserved_storage_bytes == 64

        failure_store = BlockingObjectStore()
        failure_store.error = ObjectStoreUnavailable()
        failure_service = UploadCreationService(
            session_factory=session_factory,
            settings=settings.upload,
            object_store=failure_store,
            documents_bucket=settings.object_store.documents_bucket,
        )
        failure_owner = asyncio.create_task(
            failure_service.create(
                principal=principal.context,
                idempotency_key="initializing-failure-key",
                request=request,
            )
        )
        await failure_store.create_started.wait()
        failure_replay = asyncio.create_task(
            failure_service.create(
                principal=principal.context,
                idempotency_key="initializing-failure-key",
                request=request,
            )
        )
        failure_outcomes: list[object]
        try:
            await asyncio.sleep(0.05)
            assert not failure_replay.done()
        finally:
            failure_store.release_create.set()
            failure_outcomes = list(
                await asyncio.gather(
                    failure_owner,
                    failure_replay,
                    return_exceptions=True,
                )
            )
        assert any(isinstance(result, ObjectStoreUnavailable) for result in failure_outcomes)
        assert any(isinstance(result, UploadInitializationFailed) for result in failure_outcomes)

        expiry_store = BlockingObjectStore()
        expiry_service = UploadCreationService(
            session_factory=session_factory,
            settings=settings.upload,
            object_store=expiry_store,
            documents_bucket=settings.object_store.documents_bucket,
        )
        expiry_task = asyncio.create_task(
            expiry_service.create(
                principal=principal.context,
                idempotency_key="initializing-expiry-key",
                request=request,
            )
        )
        await expiry_store.create_started.wait()
        async with session_factory.begin() as session:
            expiring_session = await session.scalar(
                select(UploadSession)
                .where(
                    UploadSession.tenant_id == principal.tenant_id,
                    UploadSession.idempotency_key == "initializing-expiry-key",
                )
                .with_for_update()
            )
            tenant = await session.scalar(
                select(Tenant).where(Tenant.id == principal.tenant_id).with_for_update()
            )
            assert expiring_session is not None and tenant is not None
            tenant.reserved_storage_bytes -= expiring_session.reserved_bytes
            expiring_session.reserved_bytes = 0
            expiring_session.status = UploadSessionStatus.EXPIRED.value
        expiry_store.release_create.set()
        with pytest.raises(UploadInitializationFailed):
            await expiry_task
        assert len(expiry_store.aborted) == 1
    finally:
        object_store.release_create.set()
        pending = [task for task in (first_task, second_task) if task is not None]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        async with session_factory.begin() as session:
            await session.execute(
                delete(UploadSession).where(UploadSession.tenant_id == principal.tenant_id)
            )
            await session.execute(
                delete(Membership).where(Membership.id == principal.membership_id)
            )
            await session.execute(delete(User).where(User.id == principal.actor_id))
            await session.execute(delete(Tenant).where(Tenant.id == principal.tenant_id))
        await engine.dispose()


@pytest.mark.integration
async def test_create_recovers_unknown_commit_results_without_destroying_active_uploads() -> None:
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    session_factory = create_session_factory(engine)
    reservation = _principal(label="reservation-commit", quota_bytes=1_000)
    activation = _principal(label="activation-commit", quota_bytes=1_000)
    unknown_activation = _principal(label="activation-read", quota_bytes=1_000)
    abort_failure = _principal(label="activation-abort", quota_bytes=1_000)
    principals = (reservation, activation, unknown_activation, abort_failure)
    request = CreateUploadSessionInput(
        filename="commit.txt",
        size_bytes=64,
        media_type="text/plain",
        sha256="a" * 64,
    )

    try:
        async with session_factory.begin() as session:
            for principal in principals:
                session.add(
                    Tenant(
                        id=principal.tenant_id,
                        name=f"Commit Outcome {principal.token}",
                        slug=f"upload-{principal.tenant_id}",
                        quota_bytes=principal.quota_bytes,
                    )
                )
                session.add(
                    User(
                        id=principal.actor_id,
                        email=f"{principal.actor_id}@example.test",
                    )
                )
            await session.flush()
            for principal in principals:
                session.add(
                    Membership(
                        id=principal.membership_id,
                        tenant_id=principal.tenant_id,
                        user_id=principal.actor_id,
                        role=MembershipRole.OWNER.value,
                    )
                )

        reservation_store = CountingObjectStore()
        reservation_factory = CommitOutcomeUnknownSessionFactory(
            session_factory,
            fail_on_begin=1,
        )
        reservation_service = UploadCreationService(
            session_factory=reservation_factory,
            settings=settings.upload,
            object_store=reservation_store,
            documents_bucket=settings.object_store.documents_bucket,
        )
        reservation_result = await reservation_service.create(
            principal=reservation.context,
            idempotency_key="reservation-commit-key",
            request=request,
        )
        reservation_replay = await reservation_service.create(
            principal=reservation.context,
            idempotency_key="reservation-commit-key",
            request=request,
        )

        activation_store = CountingObjectStore()
        activation_factory = CommitOutcomeUnknownSessionFactory(
            session_factory,
            fail_on_begin=2,
        )
        activation_service = UploadCreationService(
            session_factory=activation_factory,
            settings=settings.upload,
            object_store=activation_store,
            documents_bucket=settings.object_store.documents_bucket,
        )
        activation_result = await activation_service.create(
            principal=activation.context,
            idempotency_key="activation-commit-key",
            request=request,
        )
        activation_replay = await activation_service.create(
            principal=activation.context,
            idempotency_key="activation-commit-key",
            request=request,
        )

        unknown_store = CountingObjectStore()
        unknown_factory = CommitOutcomeUnknownSessionFactory(
            session_factory,
            fail_on_begin=2,
            fail_on_call=1,
        )
        unknown_service = UploadCreationService(
            session_factory=unknown_factory,
            settings=settings.upload,
            object_store=unknown_store,
            documents_bucket=settings.object_store.documents_bucket,
        )
        with pytest.raises(UploadInitializationFailed):
            await unknown_service.create(
                principal=unknown_activation.context,
                idempotency_key="activation-read-key",
                request=request,
            )
        unknown_replay = await UploadCreationService(
            session_factory=session_factory,
            settings=settings.upload,
            object_store=unknown_store,
            documents_bucket=settings.object_store.documents_bucket,
        ).create(
            principal=unknown_activation.context,
            idempotency_key="activation-read-key",
            request=request,
        )

        abort_failure_store = CountingObjectStore()
        abort_failure_store.abort_error = ObjectStoreUnavailable()
        abort_failure_service = UploadCreationService(
            session_factory=CommitOutcomeUnknownSessionFactory(
                session_factory,
                fail_before_commit_on_begin=2,
            ),
            settings=settings.upload,
            object_store=abort_failure_store,
            documents_bucket=settings.object_store.documents_bucket,
        )
        with pytest.raises(UploadInitializationFailed):
            await abort_failure_service.create(
                principal=abort_failure.context,
                idempotency_key="activation-abort-key",
                request=request,
            )

        async with session_factory() as session:
            failed_session = await session.scalar(
                select(UploadSession).where(
                    UploadSession.tenant_id == abort_failure.tenant_id,
                    UploadSession.idempotency_key == "activation-abort-key",
                )
            )
            failed_tenant = await session.get(Tenant, abort_failure.tenant_id)
            assert failed_session is not None
            assert failed_session.status == UploadSessionStatus.FAILED.value
            assert failed_session.object_store_upload_id == "upload-1"
            assert failed_session.reserved_bytes == 0
            assert failed_session.last_error_code == "upload_initialization_abort_failed"
            assert failed_tenant is not None and failed_tenant.reserved_storage_bytes == 0
        abort_failure_replay = await UploadCreationService(
            session_factory=session_factory,
            settings=settings.upload,
            object_store=abort_failure_store,
            documents_bucket=settings.object_store.documents_bucket,
        ).create(
            principal=abort_failure.context,
            idempotency_key="activation-abort-key",
            request=request,
        )

        for result, replay in (
            (reservation_result, reservation_replay),
            (activation_result, activation_replay),
        ):
            assert result.status == replay.status == "active"
            assert result.session_id == replay.session_id
            assert result.replayed is False
            assert replay.replayed is True
        assert len(reservation_store.created) == 1
        assert len(activation_store.created) == 1
        assert len(unknown_store.created) == 1
        assert len(abort_failure_store.created) == 1
        assert reservation_store.aborted == []
        assert activation_store.aborted == []
        assert unknown_store.aborted == []
        assert len(abort_failure_store.aborted) == 1
        assert unknown_replay.status == "active"
        assert unknown_replay.replayed is True
        assert abort_failure_replay.status == UploadSessionStatus.FAILED.value
        assert abort_failure_replay.replayed is True

        async with session_factory() as session:
            rows = (
                await session.scalars(
                    select(UploadSession)
                    .where(UploadSession.tenant_id.in_([item.tenant_id for item in principals]))
                    .order_by(UploadSession.tenant_id)
                )
            ).all()
            assert len(rows) == 4
            assert sum(row.status == "active" for row in rows) == 3
            assert sum(row.status == UploadSessionStatus.FAILED.value for row in rows) == 1
            assert all(row.object_store_upload_id is not None for row in rows)
            tenants = [await session.get(Tenant, item.tenant_id) for item in principals]
            assert [
                tenant.reserved_storage_bytes if tenant is not None else None for tenant in tenants
            ] == [64, 64, 64, 0]
    finally:
        async with session_factory.begin() as session:
            tenant_ids = [item.tenant_id for item in principals]
            actor_ids = [item.actor_id for item in principals]
            membership_ids = [item.membership_id for item in principals]
            await session.execute(
                delete(UploadSession).where(UploadSession.tenant_id.in_(tenant_ids))
            )
            await session.execute(delete(Membership).where(Membership.id.in_(membership_ids)))
            await session.execute(delete(User).where(User.id.in_(actor_ids)))
            await session.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
        await engine.dispose()


@pytest.mark.integration
async def test_create_store_failure_releases_reservation_and_allows_retry() -> None:
    settings = ApiSettings(_env_file=None)
    engine = create_database_engine(settings.database)
    session_factory = create_session_factory(engine)
    principal = _principal(label="store-failure", quota_bytes=1_000)
    object_store = CountingObjectStore()
    service = UploadCreationService(
        session_factory=session_factory,
        settings=settings.upload,
        object_store=object_store,
        documents_bucket=settings.object_store.documents_bucket,
    )

    try:
        async with session_factory.begin() as session:
            session.add(
                Tenant(
                    id=principal.tenant_id,
                    name="Upload Store Failure",
                    slug=f"upload-{principal.tenant_id}",
                    quota_bytes=principal.quota_bytes,
                )
            )
            session.add(User(id=principal.actor_id, email=f"{principal.actor_id}@example.test"))
            await session.flush()
            session.add(
                Membership(
                    id=principal.membership_id,
                    tenant_id=principal.tenant_id,
                    user_id=principal.actor_id,
                    role=MembershipRole.OWNER.value,
                )
            )

        object_store.error = ObjectStoreUnavailable()
        with pytest.raises(ObjectStoreUnavailable):
            await service.create(
                principal=principal.context,
                idempotency_key="store-failure-key",
                request=CreateUploadSessionInput(
                    filename="failure.txt",
                    size_bytes=64,
                    media_type="text/plain",
                    sha256="a" * 64,
                ),
            )

        async with session_factory() as session:
            tenant = await session.get(Tenant, principal.tenant_id)
            count = await session.scalar(
                select(func.count())
                .select_from(UploadSession)
                .where(UploadSession.tenant_id == principal.tenant_id)
            )
            assert tenant is not None and tenant.reserved_storage_bytes == 0
            assert count == 0

        object_store.error = None
        result = await service.create(
            principal=principal.context,
            idempotency_key="store-failure-key",
            request=CreateUploadSessionInput(
                filename="failure.txt",
                size_bytes=64,
                media_type="text/plain",
                sha256="a" * 64,
            ),
        )
        assert result.status == "active"
        assert len(object_store.created) == 1
    finally:
        async with session_factory.begin() as session:
            await session.execute(
                delete(UploadSession).where(UploadSession.tenant_id == principal.tenant_id)
            )
            await session.execute(
                delete(Membership).where(Membership.id == principal.membership_id)
            )
            await session.execute(delete(User).where(User.id == principal.actor_id))
            await session.execute(delete(Tenant).where(Tenant.id == principal.tenant_id))
        await engine.dispose()
