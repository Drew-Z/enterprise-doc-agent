import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Download, History, Pencil, ShieldCheck } from "lucide-react";
import type { ApiCredential } from "../auth/transport";
import { useLocale } from "../i18n";
import { AnalysisApiError, type AnalysisSnapshot, type SourceLocator } from "./api";
import { AnalysisExecutionClient, type AnalysisArtifact, type ExecutionMode, type InputSubmission, type Matrix } from "./executionApi";

const zh: Record<string, string> = { queued: "排队中", running: "取证中", waiting_input: "需要补充信息", completed: "取证完成", partial: "部分完成", failed: "执行失败", cancelled: "已取消", expired: "已到期限", succeeded: "已完成", supported: "有原文依据", conflicting: "存在冲突", insufficient: "证据不足", unreadable: "无法可靠读取" };
function location(v: SourceLocator) { return v.kind === "xlsx" ? `${v.sheet}!${v.mergedRange ?? v.cell}` : v.kind === "pdf" ? `PDF · ${v.page}` : v.kind === "txt" ? `TXT · ${v.line}` : `${v.part ?? "DOCX"} · ${v.table ? `${v.table} R${v.row}C${v.column}` : `¶${v.paragraph}`}`; }
function showValue(value: unknown): string {
  if (value === null) return "—";
  if (typeof value === "string" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.join("; ");
  if (typeof value === "object" && value !== null && "amount" in value) {
    if ("currency" in value) return `${String(value.currency)} ${String(value.amount)}`;
    if ("unit" in value && "startsWhen" in value) return `${String(value.amount)} ${String(value.unit)} · ${String(value.startsWhen)}`;
  }
  return "—";
}

type InputRequest = NonNullable<Matrix["units"][number]["inputRequest"]>;
type Unit = Matrix["units"][number];
type Field = AnalysisSnapshot["payload"]["spec"]["fields"][number];

function intentKey(kind: string, id: string) { return `${kind}:${id}:${Date.now()}:${Math.random().toString(36).slice(2)}`; }

function initialParts(unit: Unit): Record<string, string> {
  const current = unit.result?.value;
  if (Array.isArray(current)) return { value: current.join("\n") };
  if (typeof current === "object" && current !== null && "amount" in current) return {
    amount: String(current.amount),
    currency: "currency" in current ? String(current.currency) : "",
    unit: "unit" in current ? String(current.unit) : "day",
    startsWhen: "startsWhen" in current ? String(current.startsWhen) : "",
  };
  return { value: current === null || current === undefined ? "" : String(current) };
}

function manualValue(field: Field, parts: Record<string, string>): unknown {
  if (field.valueType === "boolean") return parts.value === "true";
  if (field.valueType === "list") return (parts.value ?? "").split("\n").map(value => value.trim()).filter(Boolean);
  if (field.valueType === "money") return { amount: parts.amount?.trim(), currency: parts.currency?.trim().toUpperCase() };
  if (field.valueType === "duration") return { amount: parts.amount?.trim(), unit: parts.unit, startsWhen: parts.startsWhen?.trim() };
  return parts.value?.trim();
}

function ManualValueFields({ field, parts, setParts, chinese }: { field: Field; parts: Record<string, string>; setParts: (value: Record<string, string>) => void; chinese: boolean }) {
  const update = (name: string, value: string) => setParts({ ...parts, [name]: value });
  if (field.valueType === "boolean") return <label>{chinese ? "确认值" : "Confirmed value"}<select value={parts.value ?? "true"} onChange={event => update("value", event.target.value)}><option value="true">{chinese ? "是" : "Yes"}</option><option value="false">{chinese ? "否" : "No"}</option></select></label>;
  if (field.valueType === "money") return <div className="analysis-value-grid"><label>{chinese ? "金额" : "Amount"}<input required value={parts.amount ?? ""} onChange={event => update("amount", event.target.value)} /></label><label>{chinese ? "币种" : "Currency"}<input required maxLength={3} value={parts.currency ?? ""} onChange={event => update("currency", event.target.value)} /></label></div>;
  if (field.valueType === "duration") return <><div className="analysis-value-grid"><label>{chinese ? "数量" : "Amount"}<input required value={parts.amount ?? ""} onChange={event => update("amount", event.target.value)} /></label><label>{chinese ? "单位" : "Unit"}<select value={parts.unit ?? "day"} onChange={event => update("unit", event.target.value)}>{["hour", "day", "week", "month", "year"].map(unit => <option key={unit}>{unit}</option>)}</select></label></div><label>{chinese ? "起算事件" : "Starts when"}<input required maxLength={2000} value={parts.startsWhen ?? ""} onChange={event => update("startsWhen", event.target.value)} /></label></>;
  if (field.valueType === "list") return <label>{chinese ? "确认值（每行一项）" : "Confirmed value (one item per line)"}<textarea required rows={4} maxLength={4000} value={parts.value ?? ""} onChange={event => update("value", event.target.value)} /></label>;
  return <label>{chinese ? "确认值" : "Confirmed value"}<input required maxLength={4000} type={field.valueType === "date" ? "date" : "text"} value={parts.value ?? ""} onChange={event => update("value", event.target.value)} /></label>;
}

function InputForm({ client, runId, request, chinese, onSubmitted }: { client: AnalysisExecutionClient; runId: string; request: InputRequest; chinese: boolean; onSubmitted: () => Promise<unknown> }) {
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries(request.parameters.map(parameter => [parameter.name, ""])));
  const submission = useMutation({
    mutationFn: () => client.submitInputs(runId, [{ requestId: request.id, expectedRevision: request.revision, values } satisfies InputSubmission]),
    onSuccess: onSubmitted,
  });
  const complete = request.parameters.every(parameter => values[parameter.name]?.trim());
  return <form className="analysis-input-form" onSubmit={event => { event.preventDefault(); if (complete) submission.mutate(); }}>
    {request.parameters.map(parameter => <label key={parameter.name}>{parameter.name}{parameter.unit ? ` (${parameter.unit})` : ""}<input required maxLength={2000} value={values[parameter.name] ?? ""} onChange={event => setValues(current => ({ ...current, [parameter.name]: event.target.value }))} /></label>)}
    <button type="submit" disabled={!request.canResume || !complete || submission.isPending}>{submission.isPending ? (chinese ? "正在提交…" : "Submitting…") : (chinese ? "提交并继续" : "Submit and resume")}</button>
    {!request.canResume && <p>{chinese ? "本任务已没有可用的模型请求次数；请保留待确认项并新建任务。" : "This run has no model request left. Keep the open item and start a new run."}</p>}
    {submission.isError && <p role="alert">{chinese ? "输入未提交。请刷新状态后重试。" : "The input was not submitted. Refresh the run and retry."}</p>}
  </form>;
}

function CellReviewPanel({ client, unit, field, chinese, onChanged }: { client: AnalysisExecutionClient; unit: Unit; field: Field; chinese: boolean; onChanged: () => Promise<unknown> }) {
  const queries = useQueryClient();
  const [open, setOpen] = useState(false);
  const [parts, setParts] = useState(() => initialParts(unit));
  const [reason, setReason] = useState(unit.result?.reason || "");
  const [reviewReason, setReviewReason] = useState("");
  const revisionKey = useRef(intentKey("analysis-revision", unit.id));
  const reviewKey = useRef(intentKey("analysis-review", unit.id));
  const history = useQuery({ queryKey: ["analysis-revisions", unit.id], queryFn: ({ signal }) => client.revisions(unit.id, signal), enabled: open, retry: false });
  const hasCurrentRevision = Boolean(
    unit.result && unit.currentRevision && unit.currentContentHash,
  );
  const canCreateFirstRevision = !unit.result && ["failed", "cancelled", "expired"].includes(unit.status);
  const revise = useMutation({
    mutationFn: () => client.revise(unit.id, unit.currentRevision ?? 0, revisionKey.current, { action: "final", value: manualValue(field, parts), evidenceState: "insufficient", reason, valueOrigin: "user_provided" }),
    onSuccess: async () => { revisionKey.current = intentKey("analysis-revision", unit.id); await queries.invalidateQueries({ queryKey: ["analysis-revisions", unit.id] }); await onChanged(); },
  });
  const review = useMutation({
    mutationFn: (decision: "approved" | "changes_requested") => client.review(unit.id, reviewKey.current, { revision: unit.currentRevision!, contentHash: unit.currentContentHash!, decision, reason: reviewReason }),
    onSuccess: async () => { reviewKey.current = intentKey("analysis-review", unit.id); setReviewReason(""); await queries.invalidateQueries({ queryKey: ["analysis-revisions", unit.id] }); await onChanged(); },
  });
  const unavailable = !hasCurrentRevision && !canCreateFirstRevision;
  const reviewState = unit.result?.reviewState ?? "unreviewed";
  return <details className="analysis-review-panel" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary><Pencil aria-hidden="true" />{chinese ? "修订与审核" : "Revise and review"}</summary>
    {unavailable ? <p>{chinese ? "该项尚未结束，暂不能人工补录。" : "This cell is still active and cannot be manually completed yet."}</p> : <>
      {hasCurrentRevision ? <small>{chinese ? `当前版本 ${unit.currentRevision} · ${unit.authorType === "user" ? "用户提供" : "模型原稿"} · ${reviewState === "approved" ? "已审核" : "未审核"}` : `Revision ${unit.currentRevision} · ${unit.authorType === "user" ? "user provided" : "model draft"} · ${reviewState.replaceAll("_", " ")}`}</small> : <small>{chinese ? "模型未生成有效结果；可人工建立首个用户提供版本。" : "The model produced no valid result. Create the first user-provided revision."}</small>}
      <form onSubmit={event => { event.preventDefault(); revise.mutate(); }}>
        <ManualValueFields field={field} parts={parts} setParts={setParts} chinese={chinese} />
        <label>{chinese ? "修订理由" : "Revision reason"}<textarea required rows={3} maxLength={2000} value={reason} onChange={event => setReason(event.target.value)} /></label>
        <button type="submit" disabled={revise.isPending || !reason.trim()}><Pencil aria-hidden="true" />{hasCurrentRevision ? (chinese ? "保存为新版本" : "Save new revision") : (chinese ? "保存首个版本" : "Save first revision")}</button>
      </form>
      {hasCurrentRevision && !unit.review && <div className="analysis-review-decision"><label>{chinese ? "审核理由" : "Review reason"}<textarea required rows={3} maxLength={2000} value={reviewReason} onChange={event => setReviewReason(event.target.value)} /></label><div><button type="button" disabled={review.isPending || !reviewReason.trim()} onClick={() => review.mutate("approved")}><Check aria-hidden="true" />{chinese ? "批准此版本" : "Approve revision"}</button><button type="button" disabled={review.isPending || !reviewReason.trim()} onClick={() => review.mutate("changes_requested")}>{chinese ? "要求修改" : "Request changes"}</button></div></div>}
      {unit.review && <p className="analysis-review-state"><ShieldCheck aria-hidden="true" />{chinese ? `此版本：${unit.review.decision}` : `This revision: ${unit.review.decision.replaceAll("_", " ")}`}</p>}
      {(revise.isError || review.isError) && <p role="alert">{chinese ? "操作未确认。保留当前内容并重试，或刷新核对版本。" : "The operation was not confirmed. Retry with the same content or refresh the revision."}</p>}
    </>}
    {history.isSuccess && <div className="analysis-revision-history"><h4><History aria-hidden="true" />{chinese ? "版本历史" : "Revision history"}</h4>{history.data.map(item => <div key={item.id}><strong>v{item.revision} · {item.authorType === "user" ? (chinese ? "用户提供" : "user") : (chinese ? "模型原稿" : "model")}</strong><span>{showValue(item.result.value)}</span><small>{new Date(item.createdAt).toLocaleString()} · {item.review?.decision ?? (chinese ? "未审核" : "unreviewed")}</small></div>)}</div>}
  </details>;
}

function DeliveryPanel({ client, runId, matrix, chinese }: { client: AnalysisExecutionClient; runId: string; matrix: Matrix; chinese: boolean }) {
  const queries = useQueryClient();
  const exportKeys = useRef({ draft: intentKey("analysis-export-draft", runId), reviewed: intentKey("analysis-export-reviewed", runId) });
  const approvalKeys = useRef<Record<string, string>>({});
  const artifacts = useQuery({ queryKey: ["analysis-artifacts", runId], queryFn: ({ signal }) => client.artifacts(runId, signal), retry: false });
  const create = useMutation({
    mutationFn: (mode: "draft" | "reviewed") => client.export(runId, mode, exportKeys.current[mode]),
    onSuccess: async artifact => { exportKeys.current[artifact.mode] = intentKey(`analysis-export-${artifact.mode}`, runId); await queries.invalidateQueries({ queryKey: ["analysis-artifacts", runId] }); },
  });
  const approve = useMutation({
    mutationFn: (artifact: AnalysisArtifact) => { approvalKeys.current[artifact.id] ??= intentKey("analysis-artifact-approval", artifact.id); return client.approve(artifact, approvalKeys.current[artifact.id], chinese ? "批准此冻结工作簿用于交付。" : "Approve this frozen workbook for delivery."); },
    onSuccess: async result => { delete approvalKeys.current[result.artifactId]; await queries.invalidateQueries({ queryKey: ["analysis-artifacts", runId] }); },
  });
  const download = useMutation({ mutationFn: (id: string) => client.download(id), onSuccess: result => window.open(result.url, "_blank", "noopener,noreferrer") });
  const allApproved = matrix.units.length > 0 && matrix.units.every(unit => unit.freshness === "current" && unit.review?.decision === "approved");
  return <section className="analysis-delivery" aria-label={chinese ? "导出与交付" : "Export and delivery"}><div><h3>{chinese ? "导出与交付" : "Export and delivery"}</h3><p>{chinese ? "草稿保留未审核与失败项；复核稿冻结每个已批准的精确版本。" : "Drafts retain open and failed cells. Reviewed exports freeze each exact approved revision."}</p></div><div className="analysis-actions"><button type="button" disabled={create.isPending} onClick={() => create.mutate("draft")}><Download aria-hidden="true" />{chinese ? "生成草稿 XLSX" : "Create draft XLSX"}</button><button type="button" disabled={create.isPending || !allApproved} onClick={() => create.mutate("reviewed")}><ShieldCheck aria-hidden="true" />{chinese ? "生成复核稿 XLSX" : "Create reviewed XLSX"}</button></div>{!allApproved && <small>{chinese ? "复核稿需要当前矩阵的每一项均已批准。" : "Every current cell revision must be approved for a reviewed export."}</small>}{(create.isError || approve.isError || download.isError || artifacts.isError) && <p role="alert">{chinese ? "交付操作未完成。刷新后核对已有产物再重试。" : "The delivery operation was not completed. Refresh artifacts before retrying."}</p>}{artifacts.isSuccess && artifacts.data.length > 0 && <div className="analysis-artifacts">{artifacts.data.map(artifact => <article key={artifact.id}><div><strong>{artifact.filename}</strong><small>{artifact.mode === "reviewed" ? (chinese ? "复核稿" : "Reviewed") : (chinese ? "草稿" : "Draft")} · r{artifact.matrixRevision} · {artifact.status}</small></div><div>{artifact.mode === "reviewed" && artifact.status === "ready" && <button type="button" disabled={approve.isPending} onClick={() => approve.mutate(artifact)}><ShieldCheck aria-hidden="true" />{chinese ? "批准交付" : "Approve delivery"}</button>}<button type="button" disabled={download.isPending || (artifact.mode === "reviewed" && artifact.status !== "approved")} onClick={() => download.mutate(artifact.id)}><Download aria-hidden="true" />{chinese ? "下载" : "Download"}</button></div></article>)}</div>}</section>;
}

export function AnalysisMatrix({ token, contextKey, projectId, snapshot, enabled }: { token: ApiCredential; contextKey: string; projectId: string; snapshot?: AnalysisSnapshot; enabled: boolean }) {
  const locale = useLocale();
  const chinese = locale === "zh";
  const client = useMemo(() => new AnalysisExecutionClient(token), [token]);
  const queries = useQueryClient();
  const [selectedId, setSelectedId] = useState("");
  const [mode, setMode] = useState<ExecutionMode>("fixed");
  const runs = useQuery({ queryKey: ["analysis-runs", contextKey, projectId], queryFn: ({ signal }) => client.runs(projectId, signal), retry: false });
  const terminal = new Set(["completed", "partial", "failed", "cancelled", "expired"]);
  const baseRun = runs.isSuccess && snapshot ? runs.data.find(run => run.snapshotId !== snapshot.id && run.executionMode === mode && terminal.has(run.status)) : undefined;
  const key = baseRun
    ? `analysis-${mode}-v2:${snapshot?.id ?? ""}:${baseRun.id}`
    : `analysis-${mode}-v1:${snapshot?.id ?? ""}`;
  const runId = selectedId || (runs.isSuccess ? runs.data[0]?.id ?? "" : "");
  const matrix = useQuery({ queryKey: ["analysis-matrix", contextKey, runId], queryFn: ({ signal }) => client.matrix(runId, signal), enabled: !!runId, retry: false, refetchInterval: query => ["queued", "running"].includes(query.state.data?.run.status ?? "") ? 2000 : false });
  const refresh = () => Promise.all([queries.invalidateQueries({ queryKey: ["analysis-runs", contextKey, projectId] }), queries.invalidateQueries({ queryKey: ["analysis-matrix", contextKey] }), queries.invalidateQueries({ queryKey: ["analysis-artifacts", runId] })]);
  const start = useMutation({ mutationFn: () => client.start(projectId, snapshot!.id, key, mode, baseRun?.id), onSuccess: async run => { setSelectedId(run.id); await refresh(); } });
  const cancel = useMutation({ mutationFn: () => client.cancel(runId), onSuccess: refresh });
  const message = (error: unknown) => {
    if (error instanceof AnalysisApiError && /budget|limit|quota/.test(error.code)) return chinese ? "可用额度不足或尚未配置，请联系管理员。" : "The allowance is exhausted or not configured. Contact your administrator.";
    return chinese ? "操作未完成。请刷新核对已有任务后再试。" : "The operation was not confirmed. Refresh to check the existing run before retrying.";
  };
  const status = (value: string) => chinese ? zh[value] ?? value : value.replaceAll("_", " ");
  const view = matrix.isSuccess ? matrix.data : null;
  return <section className="product-section analysis-matrix" aria-label={chinese ? "取证矩阵" : "Evidence matrix"}>
    <h2>{chinese ? "取证矩阵" : "Evidence matrix"}</h2>
    <p>{chinese ? "按已确认的字段逐项查阅资料。原文引用仍需人工核对含义和适用条件，结果尚未审核。" : "Extract each confirmed field. Review citation meaning and conditions; results are unreviewed."}</p>
    {snapshot && <p>{chinese ? `本次 ${snapshot.payload.spec.objects.length * snapshot.payload.spec.fields.length} 项，最多 ${snapshot.payload.spec.objects.length * snapshot.payload.spec.fields.length * 2} 次模型请求。` : `${snapshot.payload.spec.objects.length * snapshot.payload.spec.fields.length} cells; at most ${snapshot.payload.spec.objects.length * snapshot.payload.spec.fields.length * 2} model requests.`}</p>}
    {baseRun && <p>{chinese ? "将以上一版同模式任务为基准，仅复用依赖指纹完全一致的结果；审核和交付批准不会继承。" : "The previous terminal run in this mode is the baseline. Only exact dependency matches are reused; reviews and delivery approvals are not inherited."}</p>}
    <div className="analysis-actions"><fieldset className="analysis-mode" disabled={start.isPending}><legend>{chinese ? "执行模式" : "Execution mode"}</legend>
      <label><input type="radio" name="analysis-mode" value="fixed" checked={mode === "fixed"} onChange={() => setMode("fixed")} />{chinese ? "固定流程" : "Fixed"}</label>
      <label><input type="radio" name="analysis-mode" value="agent" checked={mode === "agent"} onChange={() => setMode("agent")} />{chinese ? "有限 Agent" : "Limited Agent"}</label>
    </fieldset><button type="button" disabled={!enabled || !snapshot || start.isPending} onClick={() => start.mutate()}>{start.isPending ? (chinese ? "正在创建…" : "Starting…") : (chinese ? "开始取证／查看本版任务" : "Start / open this source run")}</button>
      <button type="button" onClick={() => void refresh()}>{chinese ? "刷新结果" : "Refresh results"}</button>
      {!!runId && <button type="button" disabled={cancel.isPending || !view || !["queued", "running", "waiting_input"].includes(view.run.status)} onClick={() => cancel.mutate()}>{chinese ? "取消未完成项" : "Cancel unfinished cells"}</button>}
    </div>
    {!enabled && <p>{chinese ? "当前未开放新取证；已有结果仍可查看。" : "New execution is disabled; existing results remain readable."}</p>}
    {(runs.isError || matrix.isError || start.isError || cancel.isError) && <p role="alert">{message(start.error ?? cancel.error ?? matrix.error ?? runs.error)}</p>}
    {runs.isSuccess && runs.data.length > 0 && <label>{chinese ? "历史任务" : "Run history"}<select value={runId} onChange={event => setSelectedId(event.target.value)}>{runs.data.map(run => <option key={run.id} value={run.id}>{new Date(run.createdAt).toLocaleString()} · {run.executionMode === "agent" ? (chinese ? "有限 Agent" : "Limited Agent") : (chinese ? "固定流程" : "Fixed")} · {status(view?.run.id === run.id ? view.run.status : run.status)}</option>)}</select></label>}
    {view && <>
      <p role="status">{view.run.executionMode === "agent" ? (chinese ? "有限 Agent" : "Limited Agent") : (chinese ? "固定流程" : "Fixed")} · {status(view.run.status)} · {view.run.finishedUnits}/{view.run.totalUnits} · {chinese ? "模型请求" : "Model requests"} {view.run.dispatches}/{view.run.maxDispatches}{view.run.baseRunId ? ` · ${chinese ? "复用" : "reused"} ${view.run.reusedUnits} · ${chinese ? "重算" : "recomputed"} ${view.run.affectedUnits}` : ""}</p>
      <div className="analysis-table-wrap"><table><thead><tr><th>{chinese ? "分析项" : "Field"}</th>{view.snapshot.payload.spec.objects.map(obj => <th key={obj.id}>{obj.name}</th>)}</tr></thead><tbody>{view.snapshot.payload.spec.fields.map(field => <tr key={field.id}><th scope="row">{field.name}</th>{view.snapshot.payload.spec.objects.map(obj => {
        const unit = view.units.find(u => u.objectId === obj.id && u.fieldId === field.id);
        return <td key={obj.id}>{unit ? <>
          <strong>{status(unit.status)}</strong>{unit.reusedFromUnitId && <small>{chinese ? "沿用上一版结果，需重新审核" : "Reused from the previous run; review again"}</small>}{unit.invalidationReasons.length > 0 && <small>{chinese ? `本版重算：${unit.invalidationReasons.join("；")}` : `Recomputed: ${unit.invalidationReasons.join("; ")}`}</small>}{unit.freshness === "stale" && <small>{chinese ? "上游已修改，该结果已过期" : "Stale after an upstream change"}</small>}{unit.result && <><small>{status(unit.result.evidenceState)}</small><p>{showValue(unit.result.value)}</p><p>{unit.result.reason}</p>
            {unit.result.conditions.map((condition, i) => <p key={i}>{condition.text}</p>)}
            {unit.result.missingParameters.length > 0 && <p>{chinese ? "需补充：" : "Needed: "}{unit.result.missingParameters.join("; ")}</p>}
            {unit.result.calculations.map(calculation => <small key={calculation.id}>{chinese ? "计算" : "Calculation"}: {calculation.expression} = {calculation.result} {calculation.resultUnit}</small>)}
            {unit.result.evidence.map((e, i) => <details key={i}><summary>{e.filename} · {location(e.locator)}</summary><blockquote>{e.quote}</blockquote></details>)}
          </>}
          {unit.inputRequest?.state === "open" && <InputForm client={client} runId={view.run.id} request={unit.inputRequest} chinese={chinese} onSubmitted={refresh} />}
          {unit.errorCode && <p>{chinese ? "该项未生成有效结果，可检查资料或联系管理员。诊断：" : "No valid result. Check sources or contact support. Code: "}{unit.errorCode}</p>}
          {unit.search && <small>{chinese ? `在 ${unit.search.searchedVersions.length} 份资料的 ${unit.search.searchedBlocks} 个片段中检索，读取 ${unit.search.returnedBlocks} 个窗口；未找到不代表资料中不存在。` : `Searched ${unit.search.searchedBlocks} blocks in ${unit.search.searchedVersions.length} files; read ${unit.search.returnedBlocks} windows. Missing is not proof of absence.`}</small>}
          {unit.search && unit.search.coverage !== "complete" && <small>{chinese ? "部分原文件内容可能未解析，请同时检查资料区的覆盖说明。" : "Some original content may not be parsed. Check source coverage notes."}</small>}
          <CellReviewPanel client={client} unit={unit} field={field} chinese={chinese} onChanged={refresh} />
        </> : "—"}</td>;
      })}</tr>)}</tbody></table></div>
      <DeliveryPanel client={client} runId={view.run.id} matrix={view} chinese={chinese} />
    </>}
  </section>;
}
