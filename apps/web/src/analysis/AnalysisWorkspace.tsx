import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
import type { ApiCredential } from "../auth/transport";
import type { DocumentInventoryItem } from "../agent/api/schemas";
import { fetchDocumentInventory } from "../product/documentsApi";
import { UploadWorkspace } from "../upload/UploadWorkspace";
import { useLocale } from "../i18n";
import { AnalysisApiError, AnalysisClient, type AnalysisField, type AnalysisParameter, type AnalysisProject, type AnalysisSnapshot, type AnalysisSpec, type SourceLocator } from "./api";
import "./analysis.css";
import { AnalysisMatrix } from "./AnalysisMatrix";

const labels = {
  zh: {
    title: "多文档审阅", intro: "把需求和各家的方案分组，确认资料版本，再核对原文位置。",
    stage: "确认资料后可逐项取证，生成带原文依据和待确认事项的矩阵。",
    ready: "已处理", loading: "正在读取资料…", unavailable: "资料暂时无法读取，请刷新后重试。", refresh: "刷新资料", new: "新建项目", project: "选择项目",
    name: "项目名称", object: "比较对象", add: "添加对象", remove: "移除", documents: "资料分组", none: "不使用", common: "共同需求",
    sourceHint: "共同需求是比较标准，各对象的资料只用于证明该对象。每个文件只能选择一个分组，最多 10 份。",
    noDocs: "还没有可选资料。请先上传文件并等待处理完成。", fields: "分析清单", save: "保存草稿", saving: "正在保存…", freeze: "确认资料版本", freezing: "正在确认…",
    history: "已确认的资料版本", noSnapshots: "保存草稿并确认资料版本后，可在这里核对原文。", revision: "版本", preview: "核对原文", back: "上一页", next: "下一页",
    complete: "完整", partial: "部分内容待核实", unknown: "完整性未知", coverage: "解析覆盖", blocks: "内容片段", original: "原文与位置", hidden: "隐藏内容", formula: "公式未经重新计算",
    disabled: "暂未开放新项目；已有资料版本仍可查看。", signin: "请先登录并选择企业。", upload: "上传资料", limits: "支持 TXT、文本 PDF、DOCX、XLSX，每份最多 10 MiB。扫描件、图片和复杂布局可能需要补充材料。",
    notReady: "处理完成后才能确认资料；旧解析版本需要重新上传。", saved: "草稿已保存。", empty: "至少选择一个分析项，且每个对象都需要资料才能确认。",
    issues: "需核实内容", question: "分析问题", file: "文件", group: "分组", quote: "原文", invalid: "请填写项目、对象、参数和分析问题，并检查依赖关系。",
    parameters: "业务参数", addParameter: "添加参数", parameterId: "参数标识", parameterName: "参数名称", parameterValue: "当前值", parameterUnit: "单位（可选）", fieldParameters: "使用参数", upstreamFields: "依赖上游分析项",
  },
  en: {
    title: "Document review", intro: "Group requirements and proposals, freeze their versions, then inspect source locations.",
    stage: "Confirm sources, then extract a matrix with citations and unresolved items.",
    ready: "Processed", loading: "Loading sources…", unavailable: "Sources could not be loaded. Refresh to retry.", refresh: "Refresh sources", new: "New project", project: "Select project",
    name: "Project name", object: "Comparison object", add: "Add object", remove: "Remove", documents: "Source groups", none: "Unused", common: "Shared requirements",
    sourceHint: "Shared requirements define the criteria. Each file can support only its assigned object. Up to 10 files.",
    noDocs: "No sources available. Upload files and wait for processing.", fields: "Analysis checklist", save: "Save draft", saving: "Saving…", freeze: "Confirm source versions", freezing: "Confirming…",
    history: "Confirmed source versions", noSnapshots: "Save and confirm a draft to inspect its sources here.", revision: "Revision", preview: "Inspect source", back: "Previous", next: "Next",
    complete: "Complete", partial: "Some content needs checking", unknown: "Completeness unknown", coverage: "Parsing coverage", blocks: "Blocks", original: "Source and location", hidden: "Hidden content", formula: "Formula has not been recalculated",
    disabled: "New projects are disabled. Existing confirmed sources remain available.", signin: "Sign in and select an enterprise first.", upload: "Upload sources", limits: "TXT, text PDF, DOCX and XLSX; up to 10 MiB per file. Scans, images and complex layouts may require additional material.",
    notReady: "Sources must finish processing. Older parser versions require uploading again.", saved: "Draft saved.", empty: "Select at least one field. Each object needs a source before confirmation.",
    issues: "Items to check", question: "Analysis question", file: "File", group: "Group", quote: "Source text", invalid: "Complete the project, parameters, questions and dependency graph.",
    parameters: "Business parameters", addParameter: "Add parameter", parameterId: "Parameter ID", parameterName: "Parameter name", parameterValue: "Current value", parameterUnit: "Unit (optional)", fieldParameters: "Uses parameters", upstreamFields: "Depends on fields",
  },
};
type Copy = typeof labels.en;

function explain(error: unknown, copy: Copy): string {
  if (error instanceof AnalysisApiError) {
    if (error.code === "analysis_source_not_ready") return copy.notReady;
    if (error.code === "analysis_revision_conflict") return copy.unavailable;
    if (error.code === "analysis_sources_required") return copy.empty;
    if (error.code === "analysis_disabled") return copy.disabled;
  }
  return copy.unavailable;
}

function location(value: SourceLocator): string {
  if (value.kind === "xlsx") return `${value.sheet ?? ""}!${value.mergedRange ?? value.cell ?? ""}`;
  if (value.kind === "pdf") return `PDF · ${value.page ?? "?"}`;
  if (value.kind === "txt") return `TXT · ${value.line ?? "?"}`;
  return `${value.part ?? "DOCX"} · ${value.table ? `${value.table} · R${value.row}C${value.column}` : `¶${value.paragraph ?? "?"}`}`;
}

function DraftEditor({ project, template, documents, disabled, pending, copy, onSave, onDirty }: {
  project?: AnalysisProject; template: AnalysisField[]; documents: DocumentInventoryItem[]; disabled: boolean; pending: boolean; copy: Copy;
  onSave: (title: string, spec: AnalysisSpec) => void;
  onDirty: () => void;
}) {
  const [title, setTitle] = useState(project?.title ?? "");
  const [objects, setObjects] = useState(project?.spec.objects ?? [{ id: "supplier_a", name: "A", sourceVersionIds: [] }]);
  const [fields, setFields] = useState(project?.spec.fields ?? template);
  const [parameters, setParameters] = useState<AnalysisParameter[]>(project?.spec.parameters ?? []);
  const [assignment, setAssignment] = useState<Record<string, string>>(() => Object.fromEntries<string>([
    ...(project?.spec.commonRequirements ?? []).map(id => [id, "__requirements"] as const),
    ...(project?.spec.objects ?? []).flatMap(item => item.sourceVersionIds.map(id => [id, item.id] as const)),
  ]));
  const [inputError, setInputError] = useState(false);
  const selected = Object.entries(assignment).filter(([, group]) => group && (group === "__requirements" || objects.some(item => item.id === group)));
  return <form className="analysis-editor" onChange={onDirty} onSubmit={event => {
    event.preventDefault();
    if (!title.trim() || objects.some(item => !item.name.trim()) || fields.length === 0 || fields.some(item => !item.question.trim()) || parameters.some(item => !item.id.match(/^[a-z][a-z0-9_]{0,63}$/) || !item.name.trim() || !item.value.trim()) || new Set(parameters.map(item => item.id)).size !== parameters.length || selected.length > 10) { setInputError(true); return; }
    setInputError(false);
    onSave(title, { schemaVersion: "analysis.spec.v2", commonRequirements: selected.filter(([, group]) => group === "__requirements").map(([id]) => id),
      objects: objects.map(item => ({ ...item, sourceVersionIds: selected.filter(([, group]) => group === item.id).map(([id]) => id) })), fields, parameters });
  }}>
    <fieldset disabled={disabled || pending}>
      <label>{copy.name}<input required maxLength={160} value={title} onChange={event => setTitle(event.target.value)} /></label>
      <div className="analysis-objects">{objects.map((item, index) => <div key={item.id}>
        <label>{copy.object} {index + 1}<input required maxLength={160} value={item.name} onChange={event => setObjects(current => current.map(value => value.id === item.id ? { ...value, name: event.target.value } : value))} /></label>
        <button type="button" disabled={objects.length === 1} onClick={() => { onDirty(); setObjects(current => current.filter(value => value.id !== item.id)); }}>{copy.remove}</button>
      </div>)}</div>
      <button type="button" disabled={objects.length >= 3} onClick={() => { onDirty(); setObjects(current => [...current, { id: `object_${Date.now()}`, name: "", sourceVersionIds: [] }]); }}>{copy.add}</button>
      <h3>{copy.documents}</h3><p>{copy.sourceHint}</p>
      {documents.length === 0 ? <p>{copy.noDocs}</p> : <div className="analysis-table-wrap"><table><thead><tr><th>{copy.file}</th><th>{copy.group}</th></tr></thead><tbody>
        {documents.map(item => <tr key={item.versionId}><td>{item.filename}<small>{item.versionStatus === "ready" ? copy.ready : item.versionStatus === "failed" ? copy.unavailable : copy.loading}</small></td><td><select aria-label={`${copy.group}: ${item.filename}`} value={assignment[item.versionId] ?? ""} onChange={event => setAssignment(current => ({ ...current, [item.versionId]: event.target.value }))}>
          <option value="">{copy.none}</option><option value="__requirements">{copy.common}</option>{objects.map(object => <option key={object.id} value={object.id}>{object.name || copy.object}</option>)}
        </select></td></tr>)}</tbody></table></div>}
      <h3>{copy.parameters}</h3>
      <div className="analysis-parameters">{parameters.map(parameter => <div className="analysis-parameter" key={parameter.id}>
        <label>{copy.parameterId}<input required maxLength={64} pattern="[a-z][a-z0-9_]*" value={parameter.id} onChange={event => { const next = event.target.value; setParameters(current => current.map(item => item.id === parameter.id ? { ...item, id: next } : item)); setFields(current => current.map(field => ({ ...field, parameterIds: field.parameterIds.map(id => id === parameter.id ? next : id) }))); }} /></label>
        <label>{copy.parameterName}<input required maxLength={160} value={parameter.name} onChange={event => setParameters(current => current.map(item => item.id === parameter.id ? { ...item, name: event.target.value } : item))} /></label>
        <label>{copy.parameterValue}<input required maxLength={2000} value={parameter.value} onChange={event => setParameters(current => current.map(item => item.id === parameter.id ? { ...item, value: event.target.value } : item))} /></label>
        <label>{copy.parameterUnit}<input maxLength={32} value={parameter.unit ?? ""} onChange={event => setParameters(current => current.map(item => item.id === parameter.id ? { ...item, unit: event.target.value.trim() || null } : item))} /></label>
        <button type="button" onClick={() => { setParameters(current => current.filter(item => item.id !== parameter.id)); setFields(current => current.map(field => ({ ...field, parameterIds: field.parameterIds.filter(id => id !== parameter.id) }))); }}>{copy.remove}</button>
      </div>)}</div>
      <button type="button" disabled={parameters.length >= 8} onClick={() => { const suffix = Date.now().toString(36); setParameters(current => [...current, { id: `parameter_${suffix}`, name: "", value: "", unit: null }]); }}>{copy.addParameter}</button>
      <h3>{copy.fields}</h3>
      {template.map(item => {
        const selectedField = fields.find(field => field.id === item.id);
        return <div className="analysis-field" key={item.id}><label><input type="checkbox" checked={!!selectedField} onChange={event => setFields(current => event.target.checked ? [...current, item] : current.filter(field => field.id !== item.id))} />{item.name}</label>
          {selectedField && <><input aria-label={`${copy.question}: ${item.name}`} required maxLength={1000} value={selectedField.question} onChange={event => setFields(current => current.map(field => field.id === item.id ? { ...field, question: event.target.value } : field))} />
            {parameters.length > 0 && <fieldset><legend>{copy.fieldParameters}</legend>{parameters.map(parameter => <label key={parameter.id}><input type="checkbox" checked={selectedField.parameterIds.includes(parameter.id)} onChange={event => setFields(current => current.map(field => field.id === item.id ? { ...field, parameterIds: event.target.checked ? [...field.parameterIds, parameter.id] : field.parameterIds.filter(id => id !== parameter.id) } : field))} />{parameter.name || parameter.id}</label>)}</fieldset>}
            {fields.findIndex(field => field.id === item.id) > 0 && <fieldset><legend>{copy.upstreamFields}</legend>{fields.slice(0, fields.findIndex(field => field.id === item.id)).map(upstream => <label key={upstream.id}><input type="checkbox" checked={selectedField.upstreamFieldIds.includes(upstream.id)} onChange={event => setFields(current => current.map(field => field.id === item.id ? { ...field, upstreamFieldIds: event.target.checked ? [...field.upstreamFieldIds, upstream.id] : field.upstreamFieldIds.filter(id => id !== upstream.id) } : field))} />{upstream.name}</label>)}</fieldset>}
          </>}</div>;
      })}
      <button className="command-button" type="submit">{pending ? copy.saving : copy.save}</button>
    </fieldset>
    {inputError && <p role="alert">{copy.invalid}</p>}
  </form>;
}

function SourceInspector({ client, snapshot, contextKey, copy }: { client: AnalysisClient; snapshot: AnalysisSnapshot; contextKey: string; copy: Copy }) {
  const locale = useLocale();
  const issueText: Record<string, string> = locale === "zh" ? {
    needs_ocr: "本页未提取到文本，需要提供可读文字或 OCR 结果。",
    pdf_visual_content_unverified: "本页的图片或视觉内容尚未核实。",
    docx_unsupported_content: "文档包含未完整解析的图片、修订、文本框或其他内容。",
    xlsx_unsupported_content: "工作簿包含未完整解析的图形或外部内容。",
    formula_cache_missing: "公式没有可用缓存值，不能作为已验证数值。",
    formula_cache_unverified: "公式缓存可能过期，尚未重新计算。",
    spreadsheet_error_value: "单元格包含错误值。",
  } : {
    needs_ocr: "No text was extracted from this page. Supply readable text or OCR output.",
    pdf_visual_content_unverified: "Images or visual content on this page have not been checked.",
    docx_unsupported_content: "Images, revisions, text boxes or other content may be missing.",
    xlsx_unsupported_content: "Drawing or external workbook content may be missing.",
    formula_cache_missing: "No cached formula value is available. It is not a verified number.",
    formula_cache_unverified: "The formula cache may be stale and has not been recalculated.",
    spreadsheet_error_value: "This cell contains an error value.",
  };
  const [versionId, setVersionId] = useState(snapshot.payload.sources[0]?.documentVersionId ?? "");
  const [offset, setOffset] = useState(0);
  const source = snapshot.payload.sources.find(item => item.documentVersionId === versionId);
  const blocks = useQuery({ queryKey: ["analysis-blocks", contextKey, snapshot.id, versionId, offset], queryFn: ({ signal }) => client.blocks(snapshot.id, versionId, offset, signal), enabled: !!versionId, retry: false });
  return <section className="analysis-inspector" aria-label={copy.original}>
    <h3>{copy.original}</h3>
    <label>{copy.file}<select aria-label={copy.file} value={versionId} onChange={event => { setVersionId(event.target.value); setOffset(0); }}>{snapshot.payload.sources.map(item => <option key={item.documentVersionId} value={item.documentVersionId}>{item.filename}</option>)}</select></label>
    {source && <p>{copy.coverage}: {copy[source.coverage]} · {source.blockCount} {copy.blocks}</p>}
    {blocks.isPending && <p role="status">{copy.loading}</p>}
    {blocks.isError && <p role="alert">{explain(blocks.error, copy)}</p>}
    {blocks.isSuccess && <>
      {blocks.data.source.issues.length > 0 && <details className="analysis-warning"><summary>{copy.issues}: {blocks.data.source.issues.length}</summary>
        <ul>{blocks.data.source.issues.slice(0, 50).map((item, index) => <li key={index}>{location(item.locator)} · {issueText[item.code] ?? copy.unknown}</li>)}</ul>
        {blocks.data.source.issues.length > 50 && <p>{locale === "zh" ? "仅显示前 50 项；这份资料仍需完整复核。" : "Showing the first 50 items. This source still requires full review."}</p>}
      </details>}
      <ol className="analysis-blocks">{blocks.data.blocks.map(block => <li key={block.blockId}>
        <strong>{location(block.locator)}{block.locator.hidden ? ` · ${copy.hidden}` : ""}</strong>
        {block.formula && <small>{copy.formula}</small>}<p>{block.text}</p>
      </li>)}</ol>
      <div className="analysis-actions"><button type="button" disabled={offset === 0} onClick={() => setOffset(value => Math.max(0, value - 50))}>{copy.back}</button>
        <button type="button" disabled={blocks.data.nextOffset === null} onClick={() => setOffset(blocks.data.nextOffset ?? offset)}>{copy.next}</button></div>
    </>}
  </section>;
}

export function AnalysisWorkspace({ token, contextKey, canWrite = true }: { token: ApiCredential | null; contextKey: string; canWrite?: boolean }) {
  const locale = useLocale();
  const copy = labels[locale];
  const queryClient = useQueryClient();
  const client = useMemo(() => token === null ? null : new AnalysisClient(token), [token]);
  const [selectedId, setSelectedId] = useState("");
  const [snapshotId, setSnapshotId] = useState("");
  const [newRevision, setNewRevision] = useState(0);
  const [dirty, setDirty] = useState(false);
  const capabilities = useQuery({ queryKey: ["analysis-capabilities", contextKey], queryFn: ({ signal }) => client!.capabilities(signal), enabled: !!client, retry: false });
  const projects = useQuery({ queryKey: ["analysis-projects", contextKey], queryFn: ({ signal }) => client!.projects(signal), enabled: !!client, retry: false });
  const documents = useQuery({ queryKey: ["analysis-documents", contextKey], queryFn: ({ signal }) => fetchDocumentInventory(token!, signal), enabled: !!client && capabilities.data?.admissionEnabled === true, retry: false });
  const project = projects.isSuccess ? projects.data.find(item => item.id === selectedId) : undefined;
  const snapshots = useQuery({ queryKey: ["analysis-snapshots", contextKey, project?.id], queryFn: ({ signal }) => client!.snapshots(project!.id, signal), enabled: !!client && !!project, retry: false });
  const snapshot = snapshots.isSuccess ? snapshots.data.find(item => item.id === snapshotId) ?? snapshots.data[0] : undefined;
  const saved = useMutation({ mutationFn: ({ title, spec }: { title: string; spec: AnalysisSpec }) => project ? client!.update(project, title, spec) : client!.create(title, spec),
    onSuccess: async value => { setSelectedId(value.id); setDirty(false); await queryClient.invalidateQueries({ queryKey: ["analysis-projects", contextKey] }); } });
  const frozen = useMutation({ mutationFn: () => client!.freeze(project!), onSuccess: async value => { setSnapshotId(value.id); await queryClient.invalidateQueries({ queryKey: ["analysis-snapshots", contextKey, value.projectId] }); } });
  const disabled = !canWrite || !capabilities.data?.admissionEnabled;
  const selectProject = (id: string) => { setSelectedId(id); setSnapshotId(""); setDirty(false); saved.reset(); frozen.reset(); };
  return <div className="analysis-workspace">
    <header className="product-page-header"><div><h1>{copy.title}</h1><p>{copy.intro}</p><p>{copy.stage}</p></div>
      <button type="button" className="icon-button" aria-label={copy.refresh} title={copy.refresh} disabled={!client} onClick={() => void queryClient.invalidateQueries({ predicate: query => query.queryKey[0]?.toString().startsWith("analysis-") === true })}><RefreshCw aria-hidden="true" /></button></header>
    {!client ? <p role="status">{copy.signin}</p> : <>
      {capabilities.isPending && <p role="status">{copy.loading}</p>}
      {(capabilities.isError || projects.isError || documents.isError) && <p role="alert">{copy.unavailable}</p>}
      {capabilities.isSuccess && !capabilities.data.admissionEnabled && <p role="status">{copy.disabled}</p>}
      {capabilities.isSuccess && <>
        <div className="analysis-actions"><label>{copy.project}<select aria-label={copy.project} disabled={saved.isPending || frozen.isPending} value={selectedId} onChange={event => selectProject(event.target.value)}><option value="">{copy.new}</option>{projects.isSuccess && projects.data.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
          <button type="button" disabled={disabled || saved.isPending || frozen.isPending} onClick={() => { selectProject(""); setNewRevision(value => value + 1); }}>{copy.new}</button></div>
        {!disabled && <details className="analysis-upload"><summary>{copy.upload}</summary><p>{copy.limits}</p><UploadWorkspace allowSpreadsheets onCompleted={() => void queryClient.invalidateQueries({ queryKey: ["analysis-documents", contextKey] })} /></details>}
        {(selectedId === "" || project) && <section className="product-section"><DraftEditor key={`${project?.id ?? newRevision}:${project?.revision ?? 0}`} project={project} template={capabilities.data.template} documents={documents.isSuccess ? documents.data : []} disabled={disabled || documents.isError || documents.isPending} pending={saved.isPending || frozen.isPending} copy={copy} onDirty={() => setDirty(true)} onSave={(title, spec) => saved.mutate({ title, spec })} />
          {saved.isSuccess && <p role="status">{copy.saved}</p>}{saved.isError && <p role="alert">{explain(saved.error, copy)}</p>}
          {project && <><button className="command-button" type="button" disabled={disabled || dirty || saved.isPending || frozen.isPending} onClick={() => frozen.mutate()}>{frozen.isPending ? copy.freezing : copy.freeze}</button>
            {dirty && <p>{locale === "zh" ? "请先保存草稿，再确认这一版资料。" : "Save the draft before confirming these sources."}</p>}</>}
          {frozen.isError && <p role="alert">{explain(frozen.error, copy)}</p>}
        </section>}
        {project && <section className="product-section"><h2>{copy.history}</h2>
          {snapshots.isPending && <p role="status">{copy.loading}</p>}{snapshots.isError && <p role="alert">{copy.unavailable}</p>}
          {snapshots.isSuccess && (snapshots.data.length === 0 ? <p>{copy.noSnapshots}</p> : <>
            <label>{copy.revision}<select aria-label={copy.revision} value={snapshot?.id ?? ""} onChange={event => setSnapshotId(event.target.value)}>{snapshots.data.map(item => <option key={item.id} value={item.id}>{item.projectRevision} · {item.payload.title}</option>)}</select></label>
            {snapshot && <SourceInspector key={snapshot.id} client={client} snapshot={snapshot} contextKey={contextKey} copy={copy} />}
          </>)}
        </section>}
        {project && token !== null && <AnalysisMatrix key={`${contextKey}:${project.id}`} token={token} contextKey={contextKey} projectId={project.id} snapshot={snapshot} enabled={canWrite && !dirty && capabilities.data.executionEnabled && snapshot?.projectRevision === project.revision} />}
      </>}
    </>}
  </div>;
}
