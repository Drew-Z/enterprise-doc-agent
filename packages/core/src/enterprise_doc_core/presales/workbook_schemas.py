from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

MAX_WORKBOOK_BYTES = 2 * 1024 * 1024
MAX_WORKBOOK_ROWS = 120
MAX_TENANT_WORKBOOK_BYTES = 20 * 1024 * 1024


class WorkbookModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, alias_generator=to_camel)


class WorkbookMapping(WorkbookModel):
    sheet: str = Field(min_length=1, max_length=31)
    question_column: str = Field(pattern=r"^[A-Z]{1,2}$")
    answer_column: str = Field(pattern=r"^[A-Z]{1,2}$")
    first_row: int = Field(ge=1, le=10_000, strict=True)
    last_row: int = Field(ge=1, le=10_000, strict=True)

    @model_validator(mode="after")
    def distinct_columns_and_ordered_range(self) -> Self:
        if self.question_column == self.answer_column or self.first_row > self.last_row:
            raise ValueError("Choose distinct columns and an ordered row range")
        return self


class WorkbookUpload(WorkbookModel):
    filename: str = Field(min_length=6, max_length=255)
    content_base64: str = Field(min_length=4, max_length=4 * ((MAX_WORKBOOK_BYTES + 2) // 3))
    mapping: WorkbookMapping | None = None


class WorkbookQuestion(WorkbookModel):
    row: int
    question_cell: str
    answer_cell: str
    text: str


class WorkbookSheet(WorkbookModel):
    name: str
    max_row: int
    max_column: int
    selectable: bool
    sample: list[dict[str, str]]


class WorkbookPreview(WorkbookModel):
    filename: str
    sha256: str
    sheets: list[WorkbookSheet]
    questions: list[WorkbookQuestion]


class WorkbookMetadata(WorkbookModel):
    filename: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mapping: WorkbookMapping
    rows: list[int] = Field(min_length=1, max_length=MAX_WORKBOOK_ROWS)
