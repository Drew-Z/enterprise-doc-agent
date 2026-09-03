from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ScimUserResult:
    tenant_id: UUID
    user_id: UUID
    membership_id: UUID
    binding_id: UUID
    subject: str
    email: str
    role: str
    is_active: bool
    last_modified: datetime | None = None


@dataclass(frozen=True, slots=True)
class ScimUserPage:
    total_results: int
    start_index: int
    items_per_page: int
    resources: tuple[ScimUserResult, ...]
