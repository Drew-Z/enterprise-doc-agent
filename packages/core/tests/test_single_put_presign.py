from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from enterprise_doc_core.context import PrincipalContext
from enterprise_doc_core.object_store.models import PresignedObjectUpload
from enterprise_doc_core.uploads.models import UploadSession
from enterprise_doc_core.uploads.session_service import (
    UploadSessionExpired,
    UploadSessionNotActive,
    UploadSessionService,
)

NOW = datetime(2026, 10, 5, tzinfo=UTC)


class Transaction:
    def __init__(self, row: UploadSession, *, fail_commit: bool = False) -> None:
        self.row = row
        self.fail_commit = fail_commit
        self.committed = False

    def begin(self):
        return self

    async def __aenter__(self):
        return self

    async def scalar(self, query):
        return self.row

    async def __aexit__(self, kind, error, traceback):
        if error is None:
            if self.fail_commit:
                raise RuntimeError("commit unavailable")
            self.committed = True


class Signer:
    def __init__(self, transaction: Transaction) -> None:
        self.transaction = transaction
        self.calls = []

    async def presign_object_put(self, **kwargs):
        assert not self.transaction.committed
        self.calls.append(kwargs)
        return PresignedObjectUpload("https://example.test/signed", {}, 60)


def setup_case(*, mode="single_put", status="active", fail_commit=False):
    row = UploadSession(
        id=uuid4(),
        tenant_id=uuid4(),
        actor_id=uuid4(),
        pending_version_id=uuid4(),
        status=status,
        transport=mode,
        expires_at=NOW + timedelta(minutes=5),
        object_key="owned-key",
        size_bytes=25,
        signed_put_expires_at=None,
    )
    tx = Transaction(row, fail_commit=fail_commit)
    signer = Signer(tx)
    service = UploadSessionService(
        session_factory=tx,
        object_store=signer,
        documents_bucket="documents",
        clock=lambda: NOW,
    )
    principal = PrincipalContext(
        tenant_id=str(row.tenant_id), actor_id=str(row.actor_id), role="owner"
    )
    return row, tx, signer, service, principal


async def test_signed_capability_is_returned_only_after_expiry_commit():
    row, tx, signer, service, principal = setup_case()
    result = await service.presign_single_put(principal=principal, session_id=row.id)
    assert tx.committed
    assert row.signed_put_expires_at >= NOW + timedelta(seconds=result.expires_in_seconds)
    assert signer.calls[0]["metadata"]["upload-session-id"] == str(row.id)
    assert signer.calls[0]["size_bytes"] == row.size_bytes


async def test_commit_failure_does_not_return_signed_capability():
    row, tx, signer, service, principal = setup_case(fail_commit=True)
    with pytest.raises(RuntimeError, match="commit unavailable"):
        await service.presign_single_put(principal=principal, session_id=row.id)
    assert not tx.committed
    assert len(signer.calls) == 1


async def test_refresh_does_not_shorten_previously_recorded_expiry():
    row, tx, signer, service, principal = setup_case()
    previous = NOW + timedelta(minutes=3)
    row.signed_put_expires_at = previous
    await service.presign_single_put(principal=principal, session_id=row.id)
    assert row.signed_put_expires_at == previous
    assert tx.committed and len(signer.calls) == 1


@pytest.mark.parametrize(
    "mode,status",
    [("multipart", "active"), ("single_put", "aborted"), ("single_put", "completing")],
)
async def test_wrong_transport_or_inactive_session_cannot_issue_capability(mode, status):
    row, tx, signer, service, principal = setup_case(mode=mode, status=status)
    with pytest.raises(UploadSessionNotActive):
        await service.presign_single_put(principal=principal, session_id=row.id)
    assert not signer.calls
    assert not tx.committed


async def test_expired_session_cannot_issue_capability():
    row, tx, signer, service, principal = setup_case()
    row.expires_at = NOW
    with pytest.raises(UploadSessionExpired):
        await service.presign_single_put(principal=principal, session_id=row.id)
    assert not signer.calls
    assert not tx.committed
