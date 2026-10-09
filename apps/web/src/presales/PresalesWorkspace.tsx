import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { ClipboardCheck, Download, Plus, RefreshCw } from "lucide-react";
import { z } from "zod";
import { formatApiError } from "../api/errorDisplay";
import type { ApiCredential } from "../auth/transport";
import { useLocale } from "../i18n";
import { fetchDocumentInventory } from "../product/documentsApi";
import { executionModeSchema, generationActive, PresalesApiError, presalesApi, type CreatePacket, type ExecutionMode, type ManualResponseInput, type Packet, type PresalesRow, type ReviewInput, type WorkbookImportInput } from "./api";
import { presalesCopy } from "./copy";
import { PacketForm } from "./PacketForm";
import { ResponseRow } from "./ResponseRow";
import { ManualResponseEditor } from "./ManualResponseEditor";
import { workbookCopy } from "./workbook";
import "./presales.css";

export function PresalesWorkspace({ token, contextKey, storageKey, openDocuments, readOnly = false, initialVersionId, onInitialVersionConsumed, maxRequirements = 12 }: { token: ApiCredential | null; contextKey: string; storageKey: string; openDocuments: () => void; readOnly?: boolean; initialVersionId?: string; onInitialVersionConsumed?: () => void; maxRequirements?: number }) {
  const locale = useLocale(); const c = presalesCopy(locale); const w = workbookCopy(locale);
  const api = useMemo(() => presalesApi(token ?? ""), [token]);
  const queryClient = useQueryClient();
  const [activeId, setActiveId] = useState<string | null>(() => { if (initialVersionId) return null; try { const parsed = z.string().uuid().safeParse(sessionStorage.getItem(storageKey)); return parsed.success ? parsed.data : null; } catch { return null; } });
  const [entryVersionId, setEntryVersionId] = useState(initialVersionId);
  const [formRevision, setFormRevision] = useState(0);
  const [busy, setBusy] = useState("");
  const [selectedMode, setSelectedMode] = useState<ExecutionMode | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [page, setPage] = useState(0);
  const [readRecovery, setReadRecovery] = useState<{ id: string; after: number; error: unknown; notice?: string } | null>(null);
  const [blockedId, setBlockedId] = useState<string | null>(null);
  const alive = useRef(true);
  const controller = useRef<AbortController | null>(null);
  const keys = useRef(new Map<string, { body: string; key: string }>());
  const downloads = useRef(new Map<string, () => void>());
  const enabled = Boolean(token) && !readOnly;
  const recent = useQuery({ queryKey: ["presales", contextKey, "list"], queryFn: ({ signal }) => api.list(signal), enabled, retry: false, gcTime: 0 });
  const inventory = useQuery({ queryKey: ["presales", contextKey, "sources"], queryFn: ({ signal }) => fetchDocumentInventory(token ?? "", signal), enabled: enabled && !activeId, retry: false, gcTime: 0 });
  const packet = useQuery({ queryKey: ["presales", contextKey, activeId], queryFn: ({ signal }) => api.get(activeId ?? "", signal), enabled: enabled && activeId !== null, retry: false, gcTime: 0, refetchOnWindowFocus: true, refetchInterval: query => {
    if (query.state.status === "error") {
      const failure = query.state.error;
      const transient = failure instanceof TypeError || (failure instanceof PresalesApiError && (failure.status >= 500 || failure.status === 429));
      return transient && query.state.errorUpdateCount < 3 ? 2500 * query.state.errorUpdateCount : false;
    }
    return query.state.data?.rows.some(generationActive) || (readRecovery?.id === activeId && query.state.dataUpdatedAt <= readRecovery.after) ? 2500 : false;
  } });
  useEffect(() => {
    if (!initialVersionId) return;
    try { sessionStorage.removeItem(storageKey); } catch { /* Recovery is optional. */ }
    onInitialVersionConsumed?.();
  }, [initialVersionId, onInitialVersionConsumed, storageKey]);
  useEffect(() => {
    alive.current = true;
    const pendingDownloads = downloads.current;
    return () => {
      alive.current = false;
      controller.current?.abort();
      queryClient.removeQueries({ queryKey: ["presales", contextKey] });
      for (const release of pendingDownloads.values()) release();
      pendingDownloads.clear();
    };
  }, [contextKey, queryClient]);
  const select = (id: string | null) => { if (id !== activeId) setPage(0); setActiveId(id); setEntryVersionId(undefined); if (!id) setFormRevision(current => current + 1); setBlockedId(null); setError(""); setNotice(""); setReadRecovery(current => current?.id === id ? current : null); try { if (id) sessionStorage.setItem(storageKey, id); else sessionStorage.removeItem(storageKey); } catch { /* Recovery is optional; bodies stay in memory. */ } };
  const keyFor = (operation: string, payload: unknown) => { const body = JSON.stringify(payload); const previous = keys.current.get(operation); if (previous?.body === body) return previous.key; const key = crypto.randomUUID(); keys.current.set(operation, { body, key }); return key; };
  const saveResult = (value: Packet, signal: AbortSignal) => { if (!alive.current || signal.aborted) return; queryClient.setQueryData(["presales", contextKey, value.id], value); select(value.id); };
  const hideRevokedPacket = async (failure: unknown, id: string | null, signal: AbortSignal) => {
    if (!alive.current || signal.aborted) return;
    if (failure instanceof PresalesApiError && ["presales_forbidden", "presales_not_found", "presales_source_unavailable", "presales_stale_sources"].includes(failure.code)) {
      setBlockedId(id);
      await queryClient.cancelQueries({ queryKey: ["presales", contextKey, id], exact: true });
      queryClient.removeQueries({ queryKey: ["presales", contextKey, id], exact: true });
      if (alive.current && !signal.aborted) void recent.refetch();
    }
  };
  const run = async (label: string, operation: (signal: AbortSignal) => Promise<string | void>) => {
    if (controller.current !== null) return;
    const current = new AbortController(); controller.current = current; setBusy(label); setError(""); setNotice(""); setReadRecovery(null);
    try {
      const message = await operation(current.signal);
      if (alive.current && !current.signal.aborted) setNotice(message ?? c.complete);
    } catch (failure) {
      if (alive.current && !current.signal.aborted) {
        setError(formatApiError(failure, c.error, c.requestId));
        await hideRevokedPacket(failure, activeId, current.signal);
      }
    }
    finally { if (controller.current === current) controller.current = null; if (alive.current) setBusy(""); }
  };
  const create = (payload: CreatePacket) => void run("create", async signal => {
    const value = await api.create(payload, keyFor("create", payload), signal);
    if (!alive.current || signal.aborted) return;
    saveResult(value, signal);
    await recent.refetch();
  });
  const importWorkbook = (payload: WorkbookImportInput) => void run("create", async signal => {
    const intent = { title: payload.title, sources: payload.sources, filename: payload.filename, mapping: payload.mapping, confirmedSha256: payload.confirmedSha256 };
    const value = await api.importWorkbook(payload, keyFor("import-workbook", intent), signal);
    if (!alive.current || signal.aborted) return;
    saveResult(value, signal); setPage(0); await recent.refetch();
  });
  const recoverGeneration = async (id: string, signal: AbortSignal): Promise<Packet | null> => {
    try { return await api.get(id, signal); }
    catch (failure) {
      if (signal.aborted || !alive.current || (failure instanceof PresalesApiError && failure.status < 500)) throw failure;
      // The generation POST and its recovery GET both lost their response. Hide
      // stale rows until a later successful read, without repeating the write.
      const after = queryClient.getQueryState(["presales", contextKey, id])?.dataUpdatedAt ?? 0;
      setReadRecovery({ id, after, error: failure });
      return null;
    }
  };
  const modeFor = (value: Packet): ExecutionMode | undefined => {
    const available = value.availableExecutionModes ?? [];
    const saved = value.rows.flatMap(row => row.attempts).filter(attempt => attempt.executionPolicy).sort((a, b) => b.createdAt.localeCompare(a.createdAt))[0]?.executionPolicy?.mode;
    const wanted = selectedMode ?? saved ?? "auto";
    return available.includes(wanted) ? wanted : available[0];
  };
  const generate = (value: Packet, rows: PresalesRow[]) => void run(rows.length === 1 ? rows[0].id : "all", async signal => {
    const executionMode = modeFor(value);
    if (value.generationMode === "background") {
      try {
        const receipt = rows.length === 1
          ? await api.admit(value.id, rows[0].id, keyFor("generate:" + rows[0].id, { attempts: rows[0].attempts.length, state: rows[0].state, executionMode }), signal, executionMode)
          : await api.admitBatch(value.id, rows.map(r => r.id), keyFor("batch:" + value.id, { rows: rows.map(r => ({ id: r.id, attempts: r.attempts.length, state: r.state })), executionMode }), signal, executionMode);
        if (!alive.current || signal.aborted) return;
        // Discard an older in-flight GET before starting the post-admission read.
        // The receipt confirms persistence, never a row state or generated draft.
        const queryKey = ["presales", contextKey, value.id];
        await queryClient.cancelQueries({ queryKey, exact: true });
        if (!alive.current || signal.aborted) return;
        const rejected = receipt.rejected.length ? c.batchRejected.replace("{count}", String(receipt.rejected.length)) : undefined;
        setReadRecovery({ id: value.id, after: 0, error: null, notice: rejected });
        // Clear the pre-admission cache as well: navigating away and back must
        // not expose the old pending row before the new read finishes.
        void queryClient.resetQueries({ queryKey, exact: true });
        return rejected ?? c.admitted;
      } catch (failure) {
        if (signal.aborted || !alive.current || (failure instanceof PresalesApiError && failure.status < 500)) throw failure;
        const recovered = await recoverGeneration(value.id, signal);
        if (!recovered) return c.recoveryPending;
        saveResult(recovered, signal);
        const requested = recovered.rows.filter(r => rows.some(requestedRow => requestedRow.id === r.id));
        if (requested.some(generationActive)) return c.background;
        if (requested.length === rows.length && requested.every(r => r.state === "drafted" || r.state === "failed")) return c.finished;
        throw failure;
      }
    }
    for (const row of rows) {
      if (signal.aborted) return;
      let next: Packet;
      try {
        next = await api.generate(value.id, row.id, keyFor("generate:" + row.id, { attempts: row.attempts.length, state: row.state, executionMode }), signal, false, executionMode);
      } catch (failure) {
        if (signal.aborted || !alive.current || (failure instanceof PresalesApiError && failure.status < 500)) throw failure;
        // The request may still be running or already saved after a proxy/network
        // failure. Only read here: never dispatch another billable attempt.
        const recovered = await recoverGeneration(value.id, signal);
        if (!recovered) return c.recoveryPending;
        next = recovered;
        saveResult(next, signal);
        if (next.rows.find(r => r.id === row.id)?.state === "pending") throw failure;
      }
      saveResult(next, signal);
      if (next.rows.find(r => r.id === row.id)?.state === "failed") throw new Error(c.rowError);
      if (next.rows.find(r => r.id === row.id)?.state !== "drafted") return c.background;
    }
  });
  const review = (value: Packet, row: PresalesRow, payload: ReviewInput) => void run("review:" + row.id, async signal => { const next = await api.review(value.id, row.id, payload, keyFor("review:" + row.id, payload), signal); saveResult(next, signal); });
  const manualResponse = (value: Packet, row: PresalesRow, payload: ManualResponseInput) => void run("manual:" + row.id, async signal => {
    let next: Packet;
    try { next = await api.manualResponse(value.id, row.id, payload, keyFor("manual:" + row.id, payload), signal); }
    catch (failure) {
      if (signal.aborted || !alive.current || (failure instanceof PresalesApiError && failure.status < 500)) throw failure;
      const recovered = await recoverGeneration(value.id, signal);
      if (!recovered) return c.readFailed;
      next = recovered;
      if (!next.rows.find(item => item.id === row.id)?.draft) throw failure;
    }
    await queryClient.cancelQueries({ queryKey: ["presales", contextKey, value.id], exact: true });
    saveResult(next, signal);
  });
  const manualEvidence = async (id: string, version: string, query: string, offset: number, signal: AbortSignal) => {
    try { return await api.manualEvidence(id, version, query, offset, signal); }
    catch (failure) { await hideRevokedPacket(failure, id, signal); throw failure; }
  };
  const download = (id: string, mode: "draft" | "reviewed", excel = false) => void run("export", async signal => {
    const blob = await (excel ? api.exportWorkbook(id, mode, signal) : api.export(id, mode, signal));
    if (!alive.current || signal.aborted) return;
    const url = URL.createObjectURL(blob);
    const releaseUrl = URL.revokeObjectURL.bind(URL);
    const anchor = document.createElement("a");
    anchor.href = url; anchor.download = excel ? `presales-${mode}.xlsx` : "presales-responses.csv";
    document.body.append(anchor); anchor.click(); anchor.remove();
    const timer = window.setTimeout(() => { releaseUrl(url); downloads.current.delete(url); }, 1000);
    downloads.current.set(url, () => { window.clearTimeout(timer); releaseUrl(url); });
  });
  const recovery = readRecovery?.id === activeId ? readRecovery : null;
  const awaitingRead = recovery !== null && packet.dataUpdatedAt <= recovery.after;
  const visible = packet.isError || awaitingRead || (activeId !== null && blockedId === activeId) ? undefined : packet.data;
  const pageError = error || (activeId && packet.isError ? formatApiError(packet.error, c.readFailed, c.requestId) : awaitingRead && recovery.error !== null ? formatApiError(recovery.error, c.readFailed, c.requestId) : "");
  const visibleNotice = recovery && visible ? recovery.notice ?? c.readRecovered : awaitingRead && recovery.error === null ? recovery.notice ?? c.admitted : notice === c.background && visible && !visible.rows.some(generationActive) ? c.finished : notice;
  return <section className="presales-workspace">
    <header className="product-page-header"><div><p className="eyebrow">{c.title}</p><h1>{c.title}</h1><p className="page-summary">{c.summary}</p></div><button type="button" className="presales-secondary" onClick={openDocuments}>{c.documents}</button></header>
    {!enabled ? <div className="presales-empty" role="status"><ClipboardCheck aria-hidden="true" /><p>{readOnly ? c.showcase : c.login}</p><button type="button" onClick={openDocuments}>{c.documents}</button></div> : <div className="presales-layout">
      <aside className="presales-sidebar" aria-label={c.recent}><div className="presales-sidebar-heading"><h2>{c.recent}</h2><button type="button" className="presales-icon" aria-label={c.refresh} title={c.refresh} disabled={Boolean(busy)} onClick={() => void recent.refetch()}><RefreshCw aria-hidden="true" /></button></div><button className="presales-new" type="button" disabled={Boolean(busy)} onClick={() => select(null)}><Plus aria-hidden="true" />{c.newPacket}</button>
        {recent.isPending && <p role="status">{c.loading}</p>}{recent.isError && <p role="alert" className="presales-error">{formatApiError(recent.error, c.error, c.requestId)}</p>}{!recent.isError && recent.data?.length === 0 && <p className="presales-hint">{c.empty}</p>}
        {!recent.isError && recent.data?.map(item => <button type="button" key={item.id} className={"presales-packet-link" + (activeId === item.id ? " selected" : "")} aria-current={activeId === item.id ? "true" : undefined} disabled={Boolean(busy)} onClick={() => select(item.id)}><strong>{item.title}</strong><small>{new Date(item.createdAt).toLocaleDateString()} · {item.rowCount}</small>{item.staleSources && <small>{c.stale}</small>}</button>)}
      </aside>
      <div className="presales-main" aria-busy={Boolean(busy)}>{pageError && <div className="presales-error" role="alert">{pageError}{activeId && (packet.isError || awaitingRead) && blockedId !== activeId && <><p>{c.readRecoveryHelp}</p><button type="button" disabled={Boolean(busy) || packet.fetchStatus !== "idle"} onClick={() => void packet.refetch()}>{c.retryRead}</button></>}{(packet.isError || blockedId !== null) && <button type="button" onClick={() => select(null)}>{c.reset}</button>}</div>}{visibleNotice && <p className="presales-saved" role="status">{visibleNotice}</p>}
        {(busy === "all" || visible?.rows.some(row => busy === row.id)) && <p role="status" className="presales-hint">{c.submitting}</p>}
        {visibleNotice !== c.background && visible?.rows.some(generationActive) && <p role="status" className="presales-hint">{c.background}</p>}
        {!activeId && <><h2>{c.newPacket}</h2>{inventory.isPending && <p role="status">{c.loading}</p>}{inventory.isError && <p role="alert" className="presales-error">{formatApiError(inventory.error, c.error, c.requestId)}<button type="button" onClick={() => void inventory.refetch()}>{c.refresh}</button></p>}{inventory.isSuccess && <PacketForm key={formRevision} maxRequirements={maxRequirements} documents={inventory.data} busy={Boolean(busy)} onCreate={create} openDocuments={openDocuments} initialVersionId={entryVersionId} previewWorkbook={api.previewWorkbook} onImport={importWorkbook} />}</>}
        {activeId && packet.isPending && <p role="status">{c.loading}</p>}
        {visible && <><header className="presales-packet-heading"><div><h2>{visible.title}</h2><p className="presales-hint" role="status">{visible.rows.filter(r => r.draft).length} / {visible.rows.length} {c.generatedProgress} · {visible.rows.filter(r => r.review).length} / {visible.rows.length} {c.progress}</p></div><button className="presales-icon" type="button" aria-label={c.refresh} title={c.refresh} disabled={Boolean(busy)} onClick={() => void packet.refetch()}><RefreshCw aria-hidden="true" /></button></header>
          <details className="presales-sources"><summary>{c.sourceSnapshot} ({visible.sources.length})</summary><p className="presales-hint">{c.frozen}</p>{visible.sources.map(source => <div key={source.versionId}><strong>{source.filename} · v{source.versionNumber}</strong><p>{source.applicability}</p></div>)}</details>
          {Boolean(visible.availableExecutionModes?.length) && <div className="presales-mode-picker"><label className="presales-field">{c.generationMode}<select aria-label={c.generationMode} value={modeFor(visible)} disabled={Boolean(busy)} onChange={e => setSelectedMode(executionModeSchema.parse(e.target.value))}>{visible.availableExecutionModes!.map(mode => <option key={mode} value={mode}>{mode === "deep" ? c.modeDeep : c.modeAuto}</option>)}</select></label><p className="presales-hint">{c.modeHelp}</p></div>}
          <div className="presales-toolbar"><button type="button" className="presales-primary" disabled={Boolean(busy) || !visible.rows.some(r => r.state === "pending")} onClick={() => generate(visible, visible.rows.filter(r => r.state === "pending").slice(0, 12))}>{busy === "all" ? c.generating : visible.rows.length > 12 ? w.batch : c.generateAll}</button>{visible.rows.some(r => r.state === "failed" && r.attempts.length < 3) && <button type="button" disabled={Boolean(busy)} onClick={() => generate(visible, visible.rows.filter(r => r.state === "failed" && r.attempts.length < 3).slice(0, 12))}>{c.retryFailed}</button>}<button type="button" disabled={Boolean(busy)} onClick={() => download(visible.id, "draft")}><Download aria-hidden="true" />{c.exportDraft}</button><button type="button" disabled={Boolean(busy) || !visible.rows.every(r => r.review)} onClick={() => download(visible.id, "reviewed")}><Download aria-hidden="true" />{c.exportReviewed}</button></div>
          {visible.workbook && <><p className="presales-hint">{w.saved}: {visible.workbook.filename} · {visible.workbook.mapping.sheet} · {visible.workbook.mapping.questionColumn} → {visible.workbook.mapping.answerColumn}</p><div className="presales-toolbar"><button type="button" disabled={Boolean(busy)} onClick={() => download(visible.id, "draft", true)}><Download aria-hidden="true" />{w.draft}</button><button type="button" disabled={Boolean(busy) || !visible.rows.every(row => row.review)} onClick={() => download(visible.id, "reviewed", true)}><Download aria-hidden="true" />{w.reviewed}</button></div></>}
          {visible.rows.length > 12 && <><p className="presales-hint">{w.batchHelp}</p><nav className="presales-toolbar" aria-label={w.page.replace("{page}", String(page + 1)).replace("{total}", String(Math.ceil(visible.rows.length / 12)))}><button type="button" disabled={Boolean(busy) || page === 0} onClick={() => setPage(value => value - 1)}>{w.previous}</button><span>{w.page.replace("{page}", String(page + 1)).replace("{total}", String(Math.ceil(visible.rows.length / 12)))}</span><button type="button" disabled={Boolean(busy) || (page + 1) * 12 >= visible.rows.length} onClick={() => setPage(value => value + 1)}>{w.next}</button></nav></>}
          <p className="presales-hint">{c.draftOnly}</p><div className="presales-rows">{visible.rows.slice(page * 12, (page + 1) * 12).map(row => <ResponseRow key={row.id} row={row} busy={Boolean(busy)} generating={busy === row.id || (busy === "all" && row.state === "pending")} onGenerate={() => generate(visible, [row])} onReview={payload => review(visible, row, payload)} manualEditor={<ManualResponseEditor key={row.id + ":" + row.revision} row={row} sources={visible.sources} busy={Boolean(busy)} readEvidence={(version, query, offset, signal) => manualEvidence(visible.id, version, query, offset, signal)} onSave={payload => manualResponse(visible, row, payload)} />} />)}</div>
        </>}
      </div>
    </div>}
  </section>;
}
