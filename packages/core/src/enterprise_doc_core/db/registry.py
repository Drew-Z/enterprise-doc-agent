from __future__ import annotations

from typing import Any


def register_models() -> tuple[type[Any], ...]:
    """Import every mapped model before a business session can configure mappers."""
    from enterprise_doc_core.db.metadata import REGISTERED_MODELS

    return REGISTERED_MODELS
