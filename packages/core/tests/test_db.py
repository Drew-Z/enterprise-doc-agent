from __future__ import annotations

import asyncio
import sys

from enterprise_doc_core.db import selector_event_loop_factory


def test_selector_event_loop_factory_is_psycopg_compatible_on_windows() -> None:
    loop = selector_event_loop_factory()
    try:
        assert isinstance(loop, asyncio.AbstractEventLoop)
        if sys.platform == "win32":
            assert type(loop).__name__ == "_WindowsSelectorEventLoop"
    finally:
        loop.close()
