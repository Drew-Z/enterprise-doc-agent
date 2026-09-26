from __future__ import annotations

import csv
import io
from typing import Literal

from enterprise_doc_core.presales.errors import PresalesError
from enterprise_doc_core.presales.schemas import Evidence, PacketView, PrerequisiteAssessment

STATUS_LABELS = {
    "supported": "支持",
    "conditional": "有条件支持",
    "contradicted": "不满足",
    "insufficient_evidence": "证据不足",
    "conflicting_evidence": "证据冲突",
}
PREREQUISITE_LABELS = {"met": "已满足", "unmet": "未满足", "unknown": "待确认"}


def evidence_text(citation: Evidence) -> str:
    return (
        f"{citation.filename} · {citation.document_version_id} · 页{citation.page_number or '-'} · "
        f"{citation.heading or ''} · 字符{citation.start_offset}-{citation.end_offset}: "
        f"{citation.excerpt}"
    )


def prerequisite_text(items: list[PrerequisiteAssessment] | None, citations: list[Evidence]) -> str:
    if items is None:
        return "未记录前提状态"
    if not items:
        return "无前提"
    return "\n".join(
        f"{PREREQUISITE_LABELS[item.state]}：{item.condition}\n"  # noqa: RUF001
        + "\n".join(
            f"  证据 {index + 1}：{evidence_text(citations[index])}"  # noqa: RUF001
            for index in item.citation_indexes
        )
        for item in items
    )


def safe_cell(value: str) -> str:
    # Quoting alone does not stop spreadsheet formula evaluation.
    if value.lstrip(" \t\r\n\ufeff").startswith(("=", "+", "-", "@")) or value.startswith(
        ("\t", "\r", "\n")
    ):
        return "'" + value
    return value


def export_csv(packet: PacketView, mode: Literal["draft", "reviewed"]) -> bytes:
    if mode == "reviewed" and any(row.review is None or row.draft is None for row in packet.rows):
        raise PresalesError("presales_review_required")
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(
        [
            "响应表",
            "要求编号",
            "要求原文",
            "要求位置",
            "判断",
            "响应文案",
            "响应条件",
            "待补材料",
            "原文证据",
            "资料版本与适用范围",
            "复核状态",
            "复核人",
            "复核时间",
            "复核备注",
            "原模型判断",
            "原模型文案",
            "前提状态与对应证据",
            "原模型前提状态与对应证据",
        ]
    )
    source_versions = "\n".join(
        f"{s.filename} · v{s.version_number} · {s.version_id} · "
        f"{s.content_sha256} · {s.applicability}"
        for s in packet.sources
    )
    for row in packet.rows:
        effective = row.review or row.draft
        evidence = "\n".join(evidence_text(c) for c in row.draft.citations) if row.draft else ""
        values = [
            packet.title,
            row.requirement.key,
            row.requirement.text,
            row.requirement.source_location,
            STATUS_LABELS[effective.status] if effective else "未生成",
            effective.answer if effective else "",
            "\n".join(effective.conditions) if effective else "",
            "\n".join(effective.missing_information) if effective else "",
            evidence,
            source_versions,
            "已复核" if row.review else "未复核草稿",
            str(row.review.actor_id) if row.review else "",
            row.review.reviewed_at.isoformat() if row.review else "",
            row.review.note if row.review else "",
            STATUS_LABELS[row.draft.status] if row.draft else "",
            row.draft.answer if row.draft else "",
            prerequisite_text(effective.prerequisites, row.draft.citations)
            if effective and row.draft
            else "",
            prerequisite_text(row.draft.prerequisites, row.draft.citations) if row.draft else "",
        ]
        writer.writerow([safe_cell(value) for value in values])
    return stream.getvalue().encode("utf-8-sig")
