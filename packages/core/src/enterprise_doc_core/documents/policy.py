from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from enterprise_doc_core.audit import append_audit_event
from enterprise_doc_core.documents.models import Document, DocumentAccessMode, DocumentGrant
from enterprise_doc_core.identity.models import Membership, MembershipRole, Tenant, User


class DocumentPolicyError(Exception):
    code = "document_policy_error"


class DocumentPolicyNotFound(DocumentPolicyError):
    code = "document_policy_not_found"


class DocumentPolicyForbidden(DocumentPolicyError):
    code = "document_policy_forbidden"


class DocumentGrantInvalid(DocumentPolicyError):
    code = "document_grant_invalid"


def document_visible_to_actor(
    *, tenant_id: UUID, actor_id: UUID | None = None
) -> ColumnElement[bool]:
    """SQL predicate shared by inventory, retrieval, and asynchronous Agent paths."""
    if actor_id is None:
        return Document.tenant_id == tenant_id
    active_membership = exists(
        select(Membership.id)
        .join(Tenant, Tenant.id == Membership.tenant_id)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.tenant_id == Document.tenant_id,
            Membership.user_id == actor_id,
            Membership.is_active.is_(True),
            Tenant.is_active.is_(True),
            User.is_active.is_(True),
        )
    )
    user_grant = exists(
        select(DocumentGrant.id).where(
            DocumentGrant.document_id == Document.id,
            DocumentGrant.tenant_id == tenant_id,
            DocumentGrant.grantee_user_id == actor_id,
        )
    )
    role_grant = exists(
        select(DocumentGrant.id)
        .join(Membership, Membership.tenant_id == DocumentGrant.tenant_id)
        .where(
            DocumentGrant.document_id == Document.id,
            DocumentGrant.tenant_id == tenant_id,
            DocumentGrant.grantee_role == Membership.role,
            Membership.user_id == actor_id,
            Membership.is_active.is_(True),
        )
    )
    owner_membership = exists(
        select(Membership.id).where(
            Membership.tenant_id == Document.tenant_id,
            Membership.user_id == actor_id,
            Membership.role == MembershipRole.OWNER.value,
            Membership.is_active.is_(True),
        )
    )
    return (
        (Document.tenant_id == tenant_id)
        & active_membership
        & (
            (Document.access_mode == DocumentAccessMode.TENANT.value)
            | (Document.created_by == actor_id)
            | owner_membership
            | user_grant
            | role_grant
        )
    )


@dataclass(frozen=True, slots=True)
class DocumentGrantResult:
    grant_id: UUID
    document_id: UUID
    grantee_user_id: UUID | None
    grantee_role: str | None


class DocumentPolicyService:
    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def get_document(self, *, tenant_id: UUID, actor_id: UUID, document_id: UUID) -> Document:
        async with self.session_factory() as session:
            document = await session.scalar(
                select(Document).where(
                    Document.id == document_id,
                    document_visible_to_actor(tenant_id=tenant_id, actor_id=actor_id),
                )
            )
        if document is None:
            raise DocumentPolicyNotFound()
        return document

    async def set_access_mode(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        role: str,
        document_id: UUID,
        access_mode: str,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> Document:
        try:
            mode = DocumentAccessMode(access_mode)
        except ValueError as error:
            raise DocumentGrantInvalid() from error
        async with self.session_factory.begin() as session:
            document = await session.scalar(
                select(Document)
                .where(Document.id == document_id, Document.tenant_id == tenant_id)
                .with_for_update()
            )
            if document is None:
                raise DocumentPolicyNotFound()
            if not self._can_manage(document, actor_id=actor_id, role=role):
                raise DocumentPolicyForbidden()
            document.access_mode = mode.value
            await append_audit_event(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="document.policy.updated",
                resource_type="document",
                resource_id=document.id,
                metadata={"access_mode": mode.value},
                request_id=request_id,
                correlation_id=correlation_id,
            )
            return document

    async def list_grants(
        self, *, tenant_id: UUID, actor_id: UUID, role: str, document_id: UUID
    ) -> tuple[DocumentGrantResult, ...]:
        async with self.session_factory() as session:
            document = await session.scalar(
                select(Document).where(Document.id == document_id, Document.tenant_id == tenant_id)
            )
            if document is None:
                raise DocumentPolicyNotFound()
            if not self._can_manage(document, actor_id=actor_id, role=role):
                raise DocumentPolicyForbidden()
            grants = (
                await session.scalars(
                    select(DocumentGrant)
                    .where(
                        DocumentGrant.document_id == document_id,
                        DocumentGrant.tenant_id == tenant_id,
                    )
                    .order_by(DocumentGrant.created_at, DocumentGrant.id)
                )
            ).all()
            return tuple(self._grant_result(grant) for grant in grants)

    async def add_grant(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        role: str,
        document_id: UUID,
        grantee_user_id: UUID | None = None,
        grantee_role: str | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> DocumentGrantResult:
        if (grantee_user_id is None) == (grantee_role is None):
            raise DocumentGrantInvalid()
        if grantee_role is not None and grantee_role not in {item.value for item in MembershipRole}:
            raise DocumentGrantInvalid()
        async with self.session_factory.begin() as session:
            document = await session.scalar(
                select(Document).where(Document.id == document_id, Document.tenant_id == tenant_id)
            )
            if document is None:
                raise DocumentPolicyNotFound()
            if not self._can_manage(document, actor_id=actor_id, role=role):
                raise DocumentPolicyForbidden()
            if grantee_user_id is not None:
                valid_user = await session.scalar(
                    select(Membership.id).where(
                        Membership.tenant_id == tenant_id,
                        Membership.user_id == grantee_user_id,
                        Membership.is_active.is_(True),
                    )
                )
                if valid_user is None:
                    raise DocumentGrantInvalid()
            target_predicate = (
                DocumentGrant.grantee_user_id == grantee_user_id
                if grantee_user_id is not None
                else DocumentGrant.grantee_role == grantee_role
            )
            existing = await session.scalar(
                select(DocumentGrant).where(
                    DocumentGrant.document_id == document_id,
                    DocumentGrant.tenant_id == tenant_id,
                    target_predicate,
                )
            )
            if existing is not None:
                return self._grant_result(existing)
            constraint = (
                "uq_document_grants_document_user"
                if grantee_user_id is not None
                else "uq_document_grants_document_role"
            )
            inserted_id = await session.scalar(
                insert(DocumentGrant)
                .values(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    document_id=document_id,
                    grantee_user_id=grantee_user_id,
                    grantee_role=grantee_role,
                )
                .on_conflict_do_nothing(constraint=constraint)
                .returning(DocumentGrant.id)
            )
            grant = await session.scalar(
                select(DocumentGrant).where(
                    DocumentGrant.document_id == document_id,
                    DocumentGrant.tenant_id == tenant_id,
                    target_predicate,
                )
            )
            if grant is None:
                raise DocumentPolicyError()
            if inserted_id is None:
                return self._grant_result(grant)
            await append_audit_event(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="document.grant.added",
                resource_type="document",
                resource_id=document_id,
                metadata={
                    "grant_id": str(grant.id),
                    "grantee_user_id": str(grantee_user_id) if grantee_user_id else None,
                    "grantee_role": grantee_role,
                },
                request_id=request_id,
                correlation_id=correlation_id,
            )
            return self._grant_result(grant)

    async def remove_grant(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        role: str,
        document_id: UUID,
        grant_id: UUID,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        async with self.session_factory.begin() as session:
            document = await session.scalar(
                select(Document).where(Document.id == document_id, Document.tenant_id == tenant_id)
            )
            if document is None:
                raise DocumentPolicyNotFound()
            if not self._can_manage(document, actor_id=actor_id, role=role):
                raise DocumentPolicyForbidden()
            grant = await session.scalar(
                select(DocumentGrant).where(
                    DocumentGrant.id == grant_id,
                    DocumentGrant.document_id == document_id,
                    DocumentGrant.tenant_id == tenant_id,
                )
            )
            if grant is None:
                return
            await append_audit_event(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="document.grant.removed",
                resource_type="document",
                resource_id=document_id,
                metadata={"grant_id": str(grant_id)},
                request_id=request_id,
                correlation_id=correlation_id,
            )
            await session.delete(grant)

    @staticmethod
    def _can_manage(document: Document, *, actor_id: UUID, role: str) -> bool:
        return document.created_by == actor_id or role == MembershipRole.OWNER.value

    @staticmethod
    def _grant_result(grant: DocumentGrant) -> DocumentGrantResult:
        return DocumentGrantResult(
            grant_id=grant.id,
            document_id=grant.document_id,
            grantee_user_id=grant.grantee_user_id,
            grantee_role=grant.grantee_role,
        )


__all__ = [
    "DocumentGrantInvalid",
    "DocumentGrantResult",
    "DocumentPolicyError",
    "DocumentPolicyForbidden",
    "DocumentPolicyNotFound",
    "DocumentPolicyService",
    "document_visible_to_actor",
]
