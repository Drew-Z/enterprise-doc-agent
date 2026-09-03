from collections.abc import MutableMapping
from typing import Literal

from enterprise_doc_core.agents.models import (
    AgentArtifact,
    AgentRun,
    AgentRunEvent,
    AgentRunEvidence,
    AgentRunExecution,
    ApprovalRequest,
    ToolExecution,
)
from enterprise_doc_core.audit.models import (
    AuditArchiveBatch,
    AuditEvent,
    AuditLegalHold,
    AuditRetentionPolicy,
)
from enterprise_doc_core.auth.models import LocalTokenRevocation
from enterprise_doc_core.db.base import Base
from enterprise_doc_core.documents.models import (
    Document,
    DocumentChunk,
    DocumentGrant,
    DocumentIngestionGeneration,
    DocumentVersion,
)
from enterprise_doc_core.identity.models import ExternalIdentityBinding, Membership, Tenant, User
from enterprise_doc_core.jobs.models import Job, JobAttempt, JobEvent, OutboxEvent
from enterprise_doc_core.uploads.models import UploadPart, UploadSession

REGISTERED_MODELS = (
    LocalTokenRevocation,
    AuditEvent,
    AuditArchiveBatch,
    AuditLegalHold,
    AuditRetentionPolicy,
    AgentArtifact,
    AgentRun,
    AgentRunEvent,
    AgentRunEvidence,
    AgentRunExecution,
    ApprovalRequest,
    Document,
    DocumentChunk,
    DocumentGrant,
    DocumentIngestionGeneration,
    DocumentVersion,
    ExternalIdentityBinding,
    Membership,
    Job,
    JobAttempt,
    JobEvent,
    OutboxEvent,
    Tenant,
    ToolExecution,
    UploadPart,
    UploadSession,
    User,
)

metadata = Base.metadata

LANGGRAPH_CHECKPOINT_TABLES = frozenset(
    {
        "checkpoint_migrations",
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
    }
)


def include_alembic_name(
    name: str | None,
    type_: Literal[
        "schema",
        "table",
        "column",
        "index",
        "unique_constraint",
        "foreign_key_constraint",
    ],
    _parent_names: MutableMapping[
        Literal["schema_name", "table_name", "schema_qualified_table_name"],
        str | None,
    ],
) -> bool:
    """Exclude tables owned by the official LangGraph checkpointer from autogenerate."""
    return type_ != "table" or name not in LANGGRAPH_CHECKPOINT_TABLES
