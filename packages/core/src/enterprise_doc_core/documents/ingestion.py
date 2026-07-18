from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
from typing import Protocol

from docx import Document as DocxDocument
from pypdf import PdfReader


class DocumentParseViolation(ValueError):
    """Stable, non-content-bearing parser failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ParsedSection:
    text: str
    start_offset: int
    end_offset: int
    page_number: int | None = None
    heading: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedChunk:
    chunk_index: int
    text: str
    start_offset: int
    end_offset: int
    content_sha256: str
    page_number: int | None = None
    heading: str | None = None


class EmbeddingProvider(Protocol):
    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]: ...


_MARKDOWN_HEADING = re.compile(r"^\s{0,3}(#{1,6})[ \t]+(.+?)\s*$")


def _normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFC", value)
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _parse_txt(data: bytes) -> tuple[ParsedSection, ...]:
    try:
        source = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DocumentParseViolation(
            "text_decode_failed", "text document is not valid UTF-8"
        ) from exc
    source = _normalize_text(source)
    sections: list[ParsedSection] = []
    heading: str | None = None
    body_start: int | None = None
    body_end: int | None = None
    body_lines: list[str] = []

    def flush() -> None:
        nonlocal body_start, body_end, body_lines
        text = "\n".join(body_lines)
        if text and body_start is not None and body_end is not None:
            sections.append(ParsedSection(text, body_start, body_end, None, heading))
        body_start = None
        body_end = None
        body_lines = []

    cursor = 0
    for line in source.splitlines(keepends=True):
        line_without_newline = line.rstrip("\n")
        match = _MARKDOWN_HEADING.match(line_without_newline)
        if match:
            flush()
            heading = match.group(2).strip()
        elif line_without_newline.strip():
            if body_start is None:
                body_start = cursor + len(line_without_newline) - len(line_without_newline.lstrip())
            body_lines.append(line_without_newline.strip())
            body_end = cursor + len(line_without_newline.rstrip())
        cursor += len(line)
    flush()
    if not sections and source.strip():
        text = source.strip()
        start = len(source) - len(source.lstrip())
        sections.append(ParsedSection(text, start, start + len(text), None, None))
    return tuple(sections)


def _parse_docx(data: bytes) -> tuple[ParsedSection, ...]:
    try:
        document = DocxDocument(BytesIO(data))
    except Exception as exc:  # python-docx exposes several parser-specific exceptions.
        raise DocumentParseViolation(
            "docx_parse_failed", "DOCX document could not be parsed"
        ) from exc
    sections: list[ParsedSection] = []
    heading: str | None = None
    body: list[str] = []
    body_start: int | None = None
    cursor = 0

    def flush() -> None:
        nonlocal body, body_start
        text = "\n".join(body).strip()
        if text and body_start is not None:
            sections.append(ParsedSection(text, body_start, body_start + len(text), None, heading))
        body = []
        body_start = None

    for paragraph in document.paragraphs:
        value = _normalize_text(paragraph.text).strip()
        if not value:
            cursor += len(paragraph.text) + 1
            continue
        style_name = paragraph.style.name if paragraph.style is not None else ""
        if style_name.lower().startswith("heading"):
            flush()
            heading = value
        else:
            if body_start is None:
                body_start = cursor
            body.append(value)
        cursor += len(paragraph.text) + 1
    flush()
    return tuple(sections)


def _parse_pdf(data: bytes) -> tuple[ParsedSection, ...]:
    try:
        reader = PdfReader(BytesIO(data), strict=False)
    except Exception as exc:
        raise DocumentParseViolation(
            "pdf_parse_failed", "PDF document could not be parsed"
        ) from exc
    sections: list[ParsedSection] = []
    cursor = 0
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            text = _normalize_text(page.extract_text() or "").strip()
        except Exception as exc:
            raise DocumentParseViolation(
                "pdf_text_extract_failed", "PDF text could not be extracted"
            ) from exc
        start = cursor
        end = start + len(text)
        sections.append(ParsedSection(text, start, end, page_number, None))
        cursor = end + 1
    return tuple(sections)


def parse_document_bytes(data: bytes, *, extension: str) -> tuple[ParsedSection, ...]:
    normalized_extension = extension.lower().strip()
    if not normalized_extension.startswith("."):
        normalized_extension = f".{normalized_extension}"
    if normalized_extension == ".txt":
        return _parse_txt(data)
    if normalized_extension == ".docx":
        return _parse_docx(data)
    if normalized_extension == ".pdf":
        return _parse_pdf(data)
    raise DocumentParseViolation("document_type_unsupported", "document type is not supported")


def chunk_sections(
    sections: Sequence[ParsedSection], *, max_chars: int, overlap_chars: int = 0
) -> tuple[ParsedChunk, ...]:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError("overlap_chars must be in [0, max_chars)")
    step = max_chars - overlap_chars
    chunks: list[ParsedChunk] = []
    for section in sections:
        leading = len(section.text) - len(section.text.lstrip())
        text = section.text.strip()
        if not text:
            continue
        section_start = section.start_offset + leading
        start = 0
        while start < len(text):
            piece = text[start : start + max_chars]
            if piece:
                absolute_start = section_start + start
                absolute_end = absolute_start + len(piece)
                chunks.append(
                    ParsedChunk(
                        chunk_index=len(chunks),
                        text=piece,
                        start_offset=absolute_start,
                        end_offset=absolute_end,
                        content_sha256=hashlib.sha256(piece.encode("utf-8")).hexdigest(),
                        page_number=section.page_number,
                        heading=section.heading,
                    )
                )
            if start + step >= len(text):
                break
            start += step
    return tuple(chunks)


class HashEmbeddingProvider:
    """A deterministic, local provider for tests and reproducible fixtures."""

    def __init__(self, *, dimension: int = 8) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self.dimension = dimension

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._embed_one(text) for text in texts)

    def _embed_one(self, text: str) -> tuple[float, ...]:
        values: list[float] = []
        counter = 0
        while len(values) < self.dimension:
            digest = hashlib.sha256(f"{counter}:".encode("ascii") + text.encode("utf-8")).digest()
            values.extend((byte - 127.5) / 127.5 for byte in digest)
            counter += 1
        values = values[: self.dimension]
        norm = math.sqrt(sum(value * value for value in values))
        if norm == 0:
            values[0] = 1.0
            norm = 1.0
        return tuple(value / norm for value in values)
