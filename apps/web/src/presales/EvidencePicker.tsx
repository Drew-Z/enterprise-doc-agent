import { useEffect, useRef, useState } from "react";
import { useLocale } from "../i18n";
import { formatApiError } from "../api/errorDisplay";
import type { Evidence, ManualEvidencePage, Packet } from "./api";
import { presalesCopy } from "./copy";

export type EvidenceReader = (versionId: string, query: string, offset: number, signal: AbortSignal) => Promise<ManualEvidencePage>;

export function EvidencePicker({ sources, busy, readEvidence, selected, onChange, onConfirm }: {
  sources: Packet["sources"]; busy: boolean; readEvidence: EvidenceReader;
  selected: Evidence[]; onChange: (value: Evidence[]) => void; onConfirm?: () => void;
}) {
  const c = presalesCopy(useLocale());
  const [version, setVersion] = useState(sources[0].versionId);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState<ManualEvidencePage | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => { pending.current?.abort(); }, []);
  const search = async (offset: number) => {
    pending.current?.abort();
    const controller = new AbortController(); pending.current = controller;
    setLoading(true); setPage(null); setError("");
    try {
      const result = await readEvidence(version, query.trim(), offset, controller.signal);
      if (!controller.signal.aborted) setPage(result);
    } catch (failure) {
      if (!controller.signal.aborted) setError(formatApiError(failure, c.error, c.requestId));
    } finally { if (!controller.signal.aborted) setLoading(false); }
  };
  const same = (a: Evidence, b: Evidence) => a.chunkId === b.chunkId && a.documentVersionId === b.documentVersionId && a.excerpt === b.excerpt;
  return <section>
    <p className="presales-hint">{onConfirm ? c.manualBrowseHelp : c.reviewEvidenceHelp}</p>
    <label className="presales-field">{c.manualSource}<select value={version} disabled={busy || loading} onChange={e => { setVersion(e.target.value); setPage(null); }}>{sources.map(source => <option key={source.versionId} value={source.versionId}>{source.filename} · v{source.versionNumber}</option>)}</select></label>
    <p className="presales-hint">{sources.find(source => source.versionId === version)?.applicability}</p>
    <label className="presales-field">{c.manualQuery}<input value={query} maxLength={200} disabled={busy || loading} onChange={e => { setQuery(e.target.value); setPage(null); }} /></label>
    <div className="presales-toolbar"><button type="button" disabled={busy || loading} onClick={() => void search(0)}>{c.manualSearch}</button>{page?.nextOffset !== null && page?.nextOffset !== undefined && <button type="button" disabled={busy || loading} onClick={() => void search(page.nextOffset!)}>{c.manualNext}</button>}</div>
    {loading && <p role="status">{c.loading}</p>}{error && <p role="alert">{error}</p>}
    {page?.items.length === 0 && <p>{c.manualEmpty}</p>}
    <section className="presales-evidence">{page?.items.map((item, index) => <figure key={item.chunkId}><blockquote>{item.excerpt}</blockquote><figcaption>{item.filename} · {item.heading} · {item.pageNumber}</figcaption><button type="button" disabled={busy || selected.length >= 12 || selected.some(value => same(value, item))} onClick={() => onChange([...selected, item])}>{c.manualSelect} {index + 1}</button></figure>)}</section>
    <h4>{c.manualSelected} ({selected.length} / 12)</h4>
    <ul>{selected.map((item, index) => <li key={index}>{item.filename}: {item.excerpt}<button type="button" disabled={busy || loading} onClick={() => onChange(selected.filter((_, i) => i !== index))}>{c.manualRemove} {index + 1}</button></li>)}</ul>
    {onConfirm && <button type="button" disabled={busy || loading || Boolean(error)} onClick={onConfirm}>{c.manualConfirm}</button>}
  </section>;
}
