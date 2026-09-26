from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID, uuid5


class ProductMetric(StrEnum):
    AGENT_TASK = "agent_task"
    DOCUMENT_BYTES = "document_bytes"


def document_processing_operation_id(job_id: UUID, max_attempts: int) -> UUID:
    """Automatic retries share a receipt; an explicit manual retry starts a new one."""
    return uuid5(job_id, f"document-processing:{max_attempts}")


@dataclass(frozen=True, slots=True)
class ProductQuotaView:
    metric: ProductMetric
    limit: int
    used: int
    reserved: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used - self.reserved)


@dataclass(frozen=True, slots=True)
class ProductReservationResult:
    tenant_id: UUID
    operation_id: UUID
    metric: ProductMetric
    quantity: int
    ledgered: bool
    replay: bool
    status: str
    reservation_id: UUID | None = None
    entitlement_id: UUID | None = None
