from __future__ import annotations

import asyncio
import json
import subprocess
import sys

from enterprise_doc_core.documents.ingestion import DocumentParseViolation
from enterprise_doc_core.documents.structure import (
    ParsedDocumentStructure,
    StructureLimits,
    parse_document_structure,
)

MAX_RESULT_BYTES = 16 * 1024**2


async def parse_document_isolated(
    content: bytes,
    *,
    extension: str,
    timeout_seconds: float = 30,
) -> ParsedDocumentStructure:
    """A timeout/cancellation terminates the parser instead of leaving a thread running."""
    if timeout_seconds <= 0:
        raise ValueError("Parser deadline must be positive")
    if not content or len(content) > StructureLimits().max_file_bytes:
        raise DocumentParseViolation("structure_file_limit", "Document size is outside the limit.")
    # Psycopg uses Windows' Selector loop, which has no asyncio subprocess transport.
    # Startup and pipe I/O run in threads; the parent owns and reaps the process.
    startup = asyncio.create_task(
        asyncio.to_thread(
            subprocess.Popen,
            [
                sys.executable,
                "-B",
                "-X",
                "utf8",
                "-m",
                "enterprise_doc_core.documents.structure_process",
                extension,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    )
    process = None
    communication = None
    try:
        process = await asyncio.shield(startup)
        communication = asyncio.create_task(asyncio.to_thread(process.communicate, content))
        try:
            output, _ = await asyncio.wait_for(asyncio.shield(communication), timeout_seconds)
        except TimeoutError as error:
            raise DocumentParseViolation(
                "structure_timeout", "Document parsing exceeded its deadline."
            ) from error
        if process.returncode != 0 or len(output) > MAX_RESULT_BYTES:
            raise DocumentParseViolation(
                "structure_process_failed", "Document parser did not return a bounded result."
            )
        try:
            response = json.loads(output)
            if not isinstance(response, dict):
                raise ValueError("parser response must be an object")
            if response.get("error"):
                raise DocumentParseViolation(
                    response["error"], "Document could not be parsed within the supported limits."
                )
            return ParsedDocumentStructure.model_validate(response)
        except DocumentParseViolation:
            raise
        except (ValueError, TypeError) as error:
            raise DocumentParseViolation(
                "structure_process_failed", "Document parser returned an invalid result."
            ) from error
    finally:
        if process is None:
            process = await asyncio.shield(startup)
        if process.poll() is None:
            process.kill()
        if communication is None:
            communication = asyncio.create_task(asyncio.to_thread(process.communicate))
        await asyncio.shield(communication)


def main() -> None:
    # Linux deployment has an address-space ceiling; Windows uses file/text/grid limits
    # and the parent watchdog. No claim of a Windows RSS limit is made.
    if sys.platform != "win32":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (768 * 1024**2, 768 * 1024**2))
    try:
        content = sys.stdin.buffer.read(StructureLimits().max_file_bytes + 1)
        result = parse_document_structure(content, extension=sys.argv[1])
        output = result.model_dump_json().encode("utf-8")
        if len(output) > MAX_RESULT_BYTES:
            output = b'{"error":"structure_result_limit"}'
    except DocumentParseViolation as error:
        output = json.dumps({"error": error.code}).encode("utf-8")
    except Exception:
        output = b'{"error":"structure_process_failed"}'
    sys.stdout.buffer.write(output)


if __name__ == "__main__":
    main()
