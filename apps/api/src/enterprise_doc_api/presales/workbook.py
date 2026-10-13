# ruff: noqa: RUF001
from fastapi import Request
from pydantic import BaseModel, ValidationError

from enterprise_doc_api.errors import ApiError
from enterprise_doc_core.presales.workbook_schemas import MAX_WORKBOOK_BYTES

MAX_WIRE_BYTES = 4 * ((MAX_WORKBOOK_BYTES + 2) // 3) + 32_768
WORKBOOK_ERRORS: dict[str, tuple[int, str]] = {
    "presales_workbook_size": (
        413,
        "Excel 文件最多 2 MiB；解压后最多 20 MiB、50,000 个单元格、10,000 行和 256 列。",
    ),
    "presales_workbook_invalid": (
        422,
        "无法读取此 Excel 文件。请检查文件是否完整，并另存为普通 .xlsx 后重试。",
    ),
    "presales_workbook_unsupported": (
        422,
        "仅支持普通 .xlsx；宏、加密、数字签名、嵌入对象和外部链接暂不支持。",
    ),
    "presales_workbook_mapping": (422, "所选区域没有可导入的问题。请核对工作表、列及起止行。"),
    "presales_workbook_protected": (422, "所选工作表被保护或隐藏，请选择可编辑的可见工作表。"),
    "presales_workbook_question": (
        422,
        "问题须为可见、未合并的文本单元格，每条最多 2,000 字；不支持公式或错误值。",
    ),
    "presales_workbook_target": (
        422,
        "答案区域须为空白且未合并，不得包含公式、数据验证或 Excel 表格。请改选空白答案列。",
    ),
    "presales_workbook_rows": (422, "一次最多导入 120 条问题，请缩小所选行范围。"),
    "presales_workbook_mismatch": (409, "文件或映射与确认预览不一致，请重新预览。"),
    "presales_workbook_missing": (404, "此响应表没有保存原始 Excel 文件，请使用 CSV 导出。"),
    "presales_workbook_storage_limit": (
        429,
        "当前企业保存的原始问卷已达到 20 MiB 上限，请联系管理员。",
    ),
    "presales_workbook_answer_length": (
        422,
        "有响应超过 Excel 单元格长度上限，请缩短后导出；不会截断内容。",
    ),
}


async def bounded_workbook_payload[T: BaseModel](request: Request, schema: type[T]) -> T:
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        raise ApiError(status_code=415, code="content_type_invalid", message="请提交 JSON 请求。")
    length = request.headers.get("content-length")
    if length is not None:
        if not length.isascii() or not length.isdecimal():
            raise ApiError(status_code=400, code="content_length_invalid", message="请求长度无效。")
        normalized = length.lstrip("0") or "0"
        if len(normalized) > 7 or int(normalized) > MAX_WIRE_BYTES:
            raise _size_error()
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_WIRE_BYTES:
            raise _size_error()
        body.extend(chunk)
    try:
        return schema.model_validate_json(body)
    except ValidationError:
        pass
    raise ApiError(
        status_code=422,
        code="request_validation_failed",
        message="文件或导入设置无效，请重新选择。",
    )


def _size_error() -> ApiError:
    return ApiError(
        status_code=413, code="presales_workbook_size", message="Excel 文件最多 2 MiB。"
    )
