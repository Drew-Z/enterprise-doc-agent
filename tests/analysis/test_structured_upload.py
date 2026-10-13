from __future__ import annotations

from io import BytesIO

import pytest
from openpyxl import Workbook

from enterprise_doc_core.config import UploadSettings
from enterprise_doc_core.documents.envelope import (
    DocumentEnvelopeViolation,
    validate_document_envelope,
)
from enterprise_doc_core.uploads.policy import UploadPolicyViolation, validate_upload_metadata

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_xlsx_admission_is_opt_in_and_bounded() -> None:
    values = dict(filename="报价.xlsx", size_bytes=100, media_type=XLSX, sha256="a" * 64)
    with pytest.raises(UploadPolicyViolation, match="supported"):
        validate_upload_metadata(**values, settings=UploadSettings())
    settings = UploadSettings(structured_documents_enabled=True)
    assert validate_upload_metadata(**values, settings=settings).extension == ".xlsx"
    with pytest.raises(UploadPolicyViolation) as caught:
        validate_upload_metadata(**(values | {"size_bytes": 10 * 1024**2 + 1}), settings=settings)
    assert caught.value.code == "upload_size_exceeded"


class RangeReader:
    def __init__(self, content: bytes) -> None:
        self.content = content

    async def get_range(self, *, bucket: str, key: str, start: int, end_inclusive: int) -> bytes:
        return self.content[start : end_inclusive + 1]


async def test_xlsx_envelope_checks_actual_office_members() -> None:
    buffer = BytesIO()
    workbook = Workbook()
    workbook.active["A1"] = "价格"
    workbook.save(buffer)
    workbook.close()
    content = buffer.getvalue()
    result = await validate_document_envelope(
        object_store=RangeReader(content),
        bucket="test",
        key="quote",
        size_bytes=len(content),
        extension=".xlsx",
        settings=UploadSettings(),
    )
    assert result.detected_media_type == XLSX
    with pytest.raises(DocumentEnvelopeViolation) as caught:
        await validate_document_envelope(
            object_store=RangeReader(content),
            bucket="test",
            key="quote",
            size_bytes=len(content),
            extension=".docx",
            settings=UploadSettings(),
        )
    assert caught.value.code == "document_docx_required_entry_missing"
