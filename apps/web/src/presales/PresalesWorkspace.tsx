import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { ClipboardCheck, Download, Plus, RefreshCw } from "lucide-react";
import { z } from "zod";
import { formatApiError } from "../api/errorDisplay";
import type { ApiCredential } from "../auth/transport";
import { useLocale } from "../i18n";
import { fetchDocumentInventory } from "../product/documentsApi";
import { PresalesApiError, presalesApi, type CreatePacket, type Packet, type PresalesRow, type ReviewInput } from "./api";
import { presalesCopy } from "./copy";
import { PacketForm } from "./PacketForm";
import { ResponseRow } from "./ResponseRow";
import "./presales.css";

export function PresalesWorkspace({ token, contextKey, storageKey, openDocuments, readOnly = false, initialVersionId, onInitialVersionConsumed, maxRequirements = 12 }: { token: ApiCredential | null; contextKey: string; storageKey: string; openDocuments: () => void; readOnly?: boolean; initialVersionId?: string; onInitialVersionConsumed?: () => void; maxRequirements?: number }) {
  const c = presalesCopy(useLocale());
  const api = useMemo(() => presalesApi(token ?? ""), [token]);
  const queryClient = useQueryClient();
  const [activeId, setActiveId] = useState<string | null>(() => { if (initialVersionId) return null; try { const parsed = z.string().uuid().safeParse(sessionStorage.getItem(storageKey)); return parsed.success ? parsed.data : null; } catch { return null; } });
  const [entryVersionId, setEntryVersionId] = useState(initialVersionId);
  const [formRevision, setFormRevision] = useState(0);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [blockedId, setBlockedId] = useState<string | null>(null);
  const alive = useRef(true);
  const controller = useRef<AbortController | null>(null);
  const keys = useRef(new Map<string, { body: string; key: string }>());
  const downloads = useRef(new Map<string, () => void>());
  const enabled = Boolean(token) && !readOnly;
  const recent = useQuery({ queryKey: ["presales", contextKey, "list"], queryFn: ({ signal }) => api.list(signal), enabled, retry: false, gcTime: 0 });
  const inventory = useQuery({ queryKey: ["presales", contextKey, "sources"], queryFn: ({ signal }) => fetchDocumentInventory(token ?? "", signal), enabled: enabled && !activeId, retry: false, gcTime: 0 });
  const packet = useQuery({ queryKey: ["presales", contextKey, activeId], queryFn: ({ signal }) => api.get(activeId ?? "", signal), enabled: enabled && activeId !== null, retry: false, gcTime: 0, refetchOnWindowFocus: true, refetchInterval: query => query.state.data?.rows.some(r => r.state === "running") ? 2500 : false });
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
  const select = (id: string | null) => { setActiveId(id); setEntryVersionId(undefined); if (!id) setFormRevision(current => current + 1); setBlockedId(null); setError(""); setNotice(""); try { if (id) sessionStorage.setItem(storageKey, id); else sessionStorage.removeItem(storageKey); } catch { /* Recovery is optional; bodies stay in memory. */ } };
  const keyFor = (operation: string, payload: unknown) => { const body = JSON.stringify(payload); const previous = keys.current.get(operation); if (previous?.body === body) return previous.key; const key = crypto.randomUUID(); keys.current.set(operation, { body, key }); return key; };
  const saveResult = (value: Packet, signal: AbortSignal) => { if (!alive.current || signal.aborted) return; queryClient.setQueryData(["presales", contextKey, value.id], value); select(value.id); };
  const run = async (label: string, operation: (signal: AbortSignal) => Promise<string | void>) => {
    if (controller.current !== null) return;
    const current = new AbortController(); controller.current = current; setBusy(label); setError(""); setNotice("");
    try {
      const message = await operation(current.signal);
      if (alive.current && !current.signal.aborted) setNotice(message ?? c.complete);
    } catch (failure) {
      if (alive.current && !current.signal.aborted) {
        setError(formatApiError(failure, c.error, c.requestId));
        if (failure instanceof PresalesApiError && ["presales_forbidden", "presales_not_found", "presales_source_unavailable", "presales_stale_sources"].includes(failure.code)) {
          setBlockedId(activeId);
          await queryClient.cancelQueries({ queryKey: ["presales", contextKey, activeId], exact: true });
          queryClient.removeQueries({ queryKey: ["presales", contextKey, activeId], exact: true });
          if (alive.current && !current.signal.aborted) void recent.refetch();
        }
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
  const generate = (value: Packet, rows: PresalesRow[]) => void run(rows.length === 1 ? rows[0].id : "all", async signal => {
    for (const row of rows) {
      if (signal.aborted) return;
      let next: Packet;
      try {
        next = await api.generate(value.id, row.id, keyFor("generate:" + row.id, { attempts: row.attempts.length, state: row.state }), signal);
      } catch (failure) {
        if (signal.aborted || !alive.current || (failure instanceof PresalesApiError && failure.status < 500)) throw failure;
        // The request may still be running or already saved after a proxy/network
        // failure. Only read here: never dispatch another billable attempt.
        next = await api.get(value.id, signal);
        saveResult(next, signal);
        if (next.rows.find(r => r.id === row.id)?.state === "pending") throw failure;
      }
      saveResult(next, signal);
      if (next.rows.find(r => r.id === row.id)?.state === "failed") throw new Error(c.rowError);
      if (next.rows.find(r => r.id === row.id)?.state !== "drafted") return c.waiting;
    }
  });
  const review = (value: Packet, row: PresalesRow, payload: ReviewInput) => void run("review:" + row.id, async signal => { const next = await api.review(value.id, row.id, payload, keyFor("review:" + row.id, payload), signal); saveResult(next, signal); });
  const download = (id: string, mode: "draft" | "reviewed") => void run("export", async signal => {
    const blob = await api.export(id, mode, signal);
    if (!alive.current || signal.aborted) return;
    const url = URL.createObjectURL(blob);
    const releaseUrl = URL.revokeObjectURL.bind(URL);
    const anchor = document.createElement("a");
    anchor.href = url; anchor.download = "presales-responses.csv";
    document.body.append(anchor); anchor.click(); anchor.remove();
    const timer = window.setTimeout(() => { releaseUrl(url); downloads.current.delete(url); }, 1000);
    downloads.current.set(url, () => { window.clearTimeout(timer); releaseUrl(url); });
  });
  const visible = packet.isError || (activeId !== null && blockedId === activeId) ? undefined : packet.data;
  const pageError = error || (activeId && packet.isError ? formatApiError(packet.error, c.sourceUnavailable, c.requestId) : "");
  return <section className="presales-workspace">
    <header className="product-page-header"><div><p className="eyebrow">{c.title}</p><h1>{c.title}</h1><p className="page-summary">{c.summary}</p></div><button type="button" className="presales-secondary" onClick={openDocuments}>{c.documents}</button></header>
    {!enabled ? <div className="presales-empty" role="status"><ClipboardCheck aria-hidden="true" /><p>{readOnly ? c.showcase : c.login}</p><button type="button" onClick={openDocuments}>{c.documents}</button></div> : <div className="presales-layout">
      <aside className="presales-sidebar" aria-label={c.recent}><div className="presales-sidebar-heading"><h2>{c.recent}</h2><button type="button" className="presales-icon" aria-label={c.refresh} title={c.refresh} disabled={Boolean(busy)} onClick={() => void recent.refetch()}><RefreshCw aria-hidden="true" /></button></div><button className="presales-new" type="button" disabled={Boolean(busy)} onClick={() => select(null)}><Plus aria-hidden="true" />{c.newPacket}</button>
        {recent.isPending && <p role="status">{c.loading}</p>}{recent.isError && <p role="alert" className="presales-error">{formatApiError(recent.error, c.error, c.requestId)}</p>}{!recent.isError && recent.data?.length === 0 && <p className="presales-hint">{c.empty}</p>}
        {!recent.isError && recent.data?.map(item => <button type="button" key={item.id} className={"presales-packet-link" + (activeId === item.id ? " selected" : "")} aria-current={activeId === item.id ? "true" : undefined} disabled={Boolean(busy)} onClick={() => select(item.id)}><strong>{item.title}</strong><small>{new Date(item.createdAt).toLocaleDateString()} · {item.rowCount}</small>{item.staleSources && <small>{c.stale}</small>}</button>)}
      </aside>
      <div className="presales-main" aria-busy={Boolean(busy)}>{pageError && <div className="presales-error" role="alert">{pageError}{(packet.isError || blockedId !== null) && <button type="button" onClick={() => select(null)}>{c.reset}</button>}</div>}{notice && <p className="presales-saved" role="status">{notice}</p>}
        {!activeId && <><h2>{c.newPacket}</h2>{inventory.isPending && <p role="status">{c.loading}</p>}{inventory.isError && <p role="alert" className="presales-error">{formatApiError(inventory.error, c.error, c.requestId)}<button type="button" onClick={() => void inventory.refetch()}>{c.refresh}</button></p>}{inventory.isSuccess && <PacketForm key={formRevision} maxRequirements={maxRequirements} documents={inventory.data} busy={Boolean(busy)} onCreate={create} openDocuments={openDocuments} initialVersionId={entryVersionId} />}</>}
        {activeId && packet.isPending && <p role="status">{c.loading}</p>}
        {visible && <><header className="presales-packet-heading"><div><h2>{visible.title}</h2><p className="presales-hint">{visible.rows.filter(r => r.review).length} / {visible.rows.length} {c.progress}</p></div><button className="presales-icon" type="button" aria-label={c.refresh} title={c.refresh} disabled={Boolean(busy)} onClick={() => void packet.refetch()}><RefreshCw aria-hidden="true" /></button></header>
          <details className="presales-sources"><summary>{c.sourceSnapshot} ({visible.sources.length})</summary><p className="presales-hint">{c.frozen}</p>{visible.sources.map(source => <div key={source.versionId}><strong>{source.filename} · v{source.versionNumber}</strong><p>{source.applicability}</p></div>)}</details>
          <div className="presales-toolbar"><button type="button" className="presales-primary" disabled={Boolean(busy) || !visible.rows.some(r => r.state === "pending")} onClick={() => generate(visible, visible.rows.filter(r => r.state === "pending"))}>{busy === "all" ? c.generating : c.generateAll}</button><button type="button" disabled={Boolean(busy)} onClick={() => download(visible.id, "draft")}><Download aria-hidden="true" />{c.exportDraft}</button><button type="button" disabled={Boolean(busy) || !visible.rows.every(r => r.review)} onClick={() => download(visible.id, "reviewed")}><Download aria-hidden="true" />{c.exportReviewed}</button></div>
          <p className="presales-hint">{c.draftOnly}</p><div className="presales-rows">{visible.rows.map(row => <ResponseRow key={row.id} row={row} busy={Boolean(busy)} generating={busy === row.id || (busy === "all" && row.state === "pending")} onGenerate={() => generate(visible, [row])} onReview={payload => review(visible, row, payload)} />)}</div>
        </>}
      </div>
    </div>}
  </section>;
}
