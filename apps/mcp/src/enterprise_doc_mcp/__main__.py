from __future__ import annotations

import argparse
import asyncio

from enterprise_doc_core.db import ensure_asyncio_compatibility
from enterprise_doc_mcp.server import run_stdio


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="enterprise-doc-mcp",
        description="Run the enterprise document MCP stdio server.",
    )
    parser.add_argument(
        "--stdio",
        action="store_true",
        default=False,
        help="Run the stable v1 stdio transport (the default).",
    )
    return parser


def main() -> None:
    build_parser().parse_args()
    ensure_asyncio_compatibility()
    asyncio.run(run_stdio())


if __name__ == "__main__":
    main()
