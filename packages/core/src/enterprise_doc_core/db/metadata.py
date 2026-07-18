from enterprise_doc_core.db.base import Base
from enterprise_doc_core.documents.models import Document, DocumentVersion
from enterprise_doc_core.identity.models import Membership, Tenant, User
from enterprise_doc_core.jobs.models import Job, JobAttempt, JobEvent, OutboxEvent
from enterprise_doc_core.uploads.models import UploadPart, UploadSession

REGISTERED_MODELS = (
    Document,
    DocumentVersion,
    Membership,
    Job,
    JobAttempt,
    JobEvent,
    OutboxEvent,
    Tenant,
    UploadPart,
    UploadSession,
    User,
)

metadata = Base.metadata
