from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="enterprise-doc-mcp",
        description="Run the enterprise document MCP stdio server.",
    )


def main() -> None:
    build_parser().parse_args()


if __name__ == "__main__":
    main()
