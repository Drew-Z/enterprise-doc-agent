import { z } from "zod";
import type { Locale } from "../i18n";

export const workbookMappingSchema = z.object({ sheet: z.string().min(1).max(31), questionColumn: z.string().regex(/^[A-Z]{1,2}$/), answerColumn: z.string().regex(/^[A-Z]{1,2}$/), firstRow: z.number().int().min(1).max(10000), lastRow: z.number().int().min(1).max(10000) }).strict().refine(value => value.questionColumn !== value.answerColumn && value.firstRow <= value.lastRow);
export const workbookMetadataSchema = z.object({ filename: z.string(), sha256: z.string().regex(/^[0-9a-f]{64}$/), mapping: workbookMappingSchema, rows: z.array(z.number().int().positive()).min(1).max(120) }).strict();
export const workbookPreviewSchema = z.object({ filename: z.string(), sha256: z.string().regex(/^[0-9a-f]{64}$/), sheets: z.array(z.object({ name: z.string(), maxRow: z.number().int(), maxColumn: z.number().int(), selectable: z.boolean(), sample: z.array(z.record(z.string(), z.string())) }).strict()).max(20), questions: z.array(z.object({ row: z.number().int(), questionCell: z.string(), answerCell: z.string(), text: z.string() }).strict()).max(120) }).strict();
export type WorkbookMapping = z.infer<typeof workbookMappingSchema>;
export type WorkbookPreview = z.infer<typeof workbookPreviewSchema>;
export type WorkbookUpload = { filename: string; contentBase64: string; mapping?: WorkbookMapping };
export type ConfirmedWorkbook = WorkbookUpload & { mapping: WorkbookMapping; confirmedSha256: string };
export const workbookMime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

export function readWorkbookFile(file: File, signal: AbortSignal): Promise<WorkbookUpload> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    const abort = () => reader.abort();
    const finish = () => signal.removeEventListener("abort", abort);
    reader.onload = () => { finish(); if (signal.aborted) { reject(new DOMException("Aborted", "AbortError")); return; } const data = reader.result; if (typeof data !== "string" || !data.includes(",")) { reject(new Error("Cannot read file")); return; } resolve({ filename: file.name, contentBase64: data.slice(data.indexOf(",") + 1) }); };
    reader.onerror = () => { finish(); reject(new Error("Cannot read file")); };
    reader.onabort = () => { finish(); reject(new DOMException("Aborted", "AbortError")); };
    if (signal.aborted) { reject(new DOMException("Aborted", "AbortError")); return; }
    signal.addEventListener("abort", abort, { once: true });
    reader.readAsDataURL(file);
  });
}

const zh = { mode: "客户要求录入方式", manual: "手动填写", excel: "导入 Excel", file: "客户 Excel 文件", help: "普通 .xlsx，最多 2 MiB、120 条问题。选择一个工作表和行范围；只回填空白答案单元格。宏、加密、保护或外部链接文件暂不支持。", sheet: "工作表", question: "问题列", answer: "答案列", first: "起始行", last: "结束行", preview: "预览所选单元格", confirm: "我已核对以下问题与答案单元格，确认按此映射导入", invalid: "请选择不超过 2 MiB 的 .xlsx 文件，并核对列和行范围。", count: "将导入 {count} 条问题", limit: "当前会话最多允许 {count} 条问题，请缩小范围。", loading: "正在读取 Excel…", error: "无法导入 Excel，请重新选择文件或检查映射。", required: "请预览并确认 Excel 映射，同时填写名称、资料适用范围并确认所选资料。", draft: "回填草稿 Excel", reviewed: "回填已复核 Excel", saved: "原始问卷", batch: "生成下一批（最多 12 条）", batchHelp: "每次最多处理 12 条；已完成内容会保留，可继续下一批。详细证据和复核记录也可导出 CSV。", previous: "上一页", next: "下一页", page: "第 {page} / {total} 页", sample: "工作表前 8 行预览（最多显示 A–L 列）" };
const en: typeof zh = { mode: "Requirement entry", manual: "Enter manually", excel: "Import Excel", file: "Customer Excel file", help: "Ordinary .xlsx, up to 2 MiB and 120 questions. Select one sheet and row range. Only empty answer cells are filled. Macros, encryption, protection and external links are unsupported.", sheet: "Worksheet", question: "Question column", answer: "Answer column", first: "First row", last: "Last row", preview: "Preview selected cells", confirm: "I checked the questions and answer cells and confirm this mapping", invalid: "Choose an .xlsx file up to 2 MiB and check the columns and row range.", count: "Importing {count} questions", limit: "This session allows up to {count} questions. Select a smaller range.", loading: "Reading Excel…", error: "Excel could not be imported. Check the file and mapping.", required: "Preview and confirm the Excel mapping, enter a name and applicability, and confirm the selected sources.", draft: "Fill draft Excel", reviewed: "Fill reviewed Excel", saved: "Original questionnaire", batch: "Generate next batch (up to 12)", batchHelp: "Process up to 12 rows per batch. Saved responses remain available as you continue. Export CSV for detailed evidence and review history.", previous: "Previous page", next: "Next page", page: "Page {page} / {total}", sample: "First 8 rows (up to columns A–L)" };
export const workbookCopy = (locale: Locale) => locale === "zh" ? zh : en;
