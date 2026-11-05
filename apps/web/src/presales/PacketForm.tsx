import { useState, type FormEvent } from "react";
import type { DocumentInventoryItem } from "../agent/api/schemas";
import { useLocale } from "../i18n";
import { createPacketSchema, type CreatePacket, type WorkbookImportInput } from "./api";
import { presalesCopy } from "./copy";
import { WorkbookImport } from "./WorkbookImport";
import { workbookCopy, type ConfirmedWorkbook, type WorkbookPreview, type WorkbookUpload } from "./workbook";

export function PacketForm({ documents, busy, onCreate, openDocuments, initialVersionId, maxRequirements = 12, previewWorkbook, onImport }: { documents: DocumentInventoryItem[]; busy: boolean; onCreate: (payload: CreatePacket) => void; openDocuments: () => void; initialVersionId?: string; maxRequirements?: number; previewWorkbook: (payload: WorkbookUpload, signal: AbortSignal) => Promise<WorkbookPreview>; onImport: (payload: WorkbookImportInput) => void }) {
  const locale = useLocale(); const c = presalesCopy(locale); const w = workbookCopy(locale);
  const ready = documents.filter(d => d.versionStatus === "ready" && d.ingestionStatus === "succeeded" && d.ingestionStage === "ready" && d.generationId !== null);
  const [title, setTitle] = useState("");
  const [scope, setScope] = useState("");
  const [requirements, setRequirements] = useState("");
  const [selected, setSelected] = useState<string[]>(() => initialVersionId && ready.some(d => d.versionId === initialVersionId) ? [initialVersionId] : []);
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState("");
  const [mode, setMode] = useState("manual");
  const [workbook, setWorkbook] = useState<ConfirmedWorkbook | null>(null);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (mode === "excel") {
      const shared = createPacketSchema.pick({ title: true, sources: true }).safeParse({ title: title.trim(), sources: selected.map(versionId => ({ versionId, applicability: scope.trim() })) });
      if (!confirmed || !shared.success || !workbook || selected.some(id => !ready.some(d => d.versionId === id))) { setError(w.required); return; }
      setError(""); onImport({ ...shared.data, ...workbook }); return;
    }
    const parsed = createPacketSchema.safeParse({
      title: title.trim(), sources: selected.map(versionId => ({ versionId, applicability: scope.trim() })),
      requirements: requirements.split(/\r?\n/).map(line => line.trim()).filter(Boolean).map((line, index) => { const [text, ...location] = line.split("\t"); return { key: "R" + (index + 1), text: text.trim(), sourceLocation: location.join(" ").trim() }; }),
    });
    if (!confirmed || !parsed.success || parsed.data.requirements.length > maxRequirements || selected.some(id => !ready.some(d => d.versionId === id))) { setError(c.invalid.replace("12", String(maxRequirements))); return; }
    setError(""); onCreate(parsed.data);
  };
  return <form className="presales-form" onSubmit={submit}>
    <label className="presales-field">{c.name}<input value={title} onChange={e => setTitle(e.target.value)} maxLength={160} required placeholder={c.namePlaceholder} disabled={busy} /></label>
    <fieldset disabled={busy}><legend>{c.sources}</legend><p className="presales-hint">{c.sourceHelp}</p>
      {initialVersionId && !ready.some(d => d.versionId === initialVersionId) && <p role="alert" className="presales-error">{c.sourceUnavailable}</p>}
      {ready.length === 0 ? <div className="presales-empty"><p>{c.noSources}</p><button type="button" onClick={openDocuments}>{c.documents}</button></div> : <div className="presales-source-options">{ready.map(document => <label key={document.versionId} className="presales-source-option"><input type="checkbox" checked={selected.includes(document.versionId)} disabled={!selected.includes(document.versionId) && selected.length >= 6} onChange={e => { setSelected(current => e.target.checked ? [...current, document.versionId] : current.filter(id => id !== document.versionId)); setConfirmed(false); }} /><span><strong>{document.filename}</strong><small>{c.version} {document.versionNumber}</small></span></label>)}</div>}
      <label className="presales-field">{c.scope}<textarea value={scope} onChange={e => { setScope(e.target.value); setConfirmed(false); }} maxLength={500} rows={2} placeholder={c.scopePlaceholder} required /></label>
      <label className="presales-confirm"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />{c.confirmSources}</label>
    </fieldset>
    <label className="presales-field">{w.mode}<select value={mode} disabled={busy} onChange={event => { setMode(event.target.value); setWorkbook(null); setError(""); }}><option value="manual">{w.manual}</option><option value="excel">{w.excel}</option></select></label>
    {mode === "excel" ? <WorkbookImport busy={busy} preview={previewWorkbook} onChange={setWorkbook} maxRows={maxRequirements < 12 ? maxRequirements : 120} /> : <fieldset disabled={busy}><legend>{c.requirements}</legend><p className="presales-hint" id="requirements-help">{c.requirementsHelp.replace("12", String(maxRequirements))}</p><textarea aria-label={c.requirements} aria-describedby="requirements-help" value={requirements} onChange={e => setRequirements(e.target.value)} rows={7} maxLength={28000} placeholder={c.requirementsPlaceholder} required /></fieldset>}
    {error && <p role="alert" className="presales-error">{error}</p>}
    <div className="presales-form-footer"><p className="presales-hint">{c.createHelp}</p><button className="presales-primary" type="submit" disabled={busy || ready.length === 0 || (mode === "excel" && !workbook)}>{busy ? c.loading : c.create}</button></div>
  </form>;
}
