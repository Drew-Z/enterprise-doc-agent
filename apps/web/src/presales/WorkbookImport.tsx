import { useEffect, useRef, useState } from "react";
import { useLocale } from "../i18n";
import { readWorkbookFile, workbookCopy, workbookMappingSchema, type ConfirmedWorkbook, type WorkbookMapping, type WorkbookPreview, type WorkbookUpload } from "./workbook";

export function WorkbookImport({ busy, preview, onChange, maxRows }: { busy: boolean; preview: (payload: WorkbookUpload, signal: AbortSignal) => Promise<WorkbookPreview>; onChange: (value: ConfirmedWorkbook | null) => void; maxRows: number }) {
  const c = workbookCopy(useLocale());
  const [upload, setUpload] = useState<WorkbookUpload | null>(null);
  const [overview, setOverview] = useState<WorkbookPreview | null>(null);
  const [checked, setChecked] = useState<WorkbookPreview | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [mapping, setMapping] = useState<WorkbookMapping>({ sheet: "", questionColumn: "B", answerColumn: "C", firstRow: 2, lastRow: 2 });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  const invalidate = () => { setChecked(null); setConfirmed(false); setError(""); onChange(null); };
  const load = async (file?: File) => {
    controller.current?.abort(); invalidate(); setUpload(null); setOverview(null); setLoading(false);
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".xlsx") || file.size > 2 * 1024 * 1024) { setError(c.invalid); return; }
    const current = new AbortController(); controller.current = current; setLoading(true);
    try {
      const value = await readWorkbookFile(file, current.signal);
      const result = await preview(value, current.signal);
      if (current.signal.aborted) return;
      setUpload(value); setOverview(result);
      const sheet = result.sheets.find(item => item.selectable);
      setMapping({ sheet: sheet?.name ?? "", questionColumn: "B", answerColumn: "C", firstRow: 2, lastRow: Math.max(2, Math.min(sheet?.maxRow ?? 2, 10000)) });
    } catch (failure) { if (!current.signal.aborted) setError(failure instanceof Error ? failure.message : c.error); }
    finally { if (!current.signal.aborted) setLoading(false); }
  };
  const verify = async () => {
    invalidate(); const parsed = workbookMappingSchema.safeParse(mapping);
    if (!upload || !parsed.success) { setError(c.invalid); return; }
    const current = new AbortController(); controller.current = current; setLoading(true);
    try {
      const result = await preview({ ...upload, mapping: parsed.data }, current.signal);
      if (current.signal.aborted) return;
      if (!result.questions.length || result.questions.length > maxRows) { setError(c.limit.replace("{count}", String(maxRows))); return; }
      setChecked(result);
    } catch (failure) { if (!current.signal.aborted) setError(failure instanceof Error ? failure.message : c.error); }
    finally { if (!current.signal.aborted) setLoading(false); }
  };
  const update = (patch: Partial<WorkbookMapping>) => { invalidate(); setMapping(current => ({ ...current, ...patch })); };
  const selectedSheet = overview?.sheets.find(sheet => sheet.name === mapping.sheet);
  return <fieldset className="presales-workbook" disabled={busy || loading}>
    <legend>{c.excel}</legend><p className="presales-hint">{c.help}</p>
    <label className="presales-field">{c.file}<input type="file" accept=".xlsx" onChange={event => void load(event.target.files?.[0])} /></label>
    {overview && <><div className="presales-workbook-mapping">
      <label className="presales-field">{c.sheet}<select aria-label={c.sheet} value={mapping.sheet} onChange={event => { const sheet = overview.sheets.find(item => item.name === event.target.value); update({ sheet: event.target.value, lastRow: Math.max(2, Math.min(sheet?.maxRow ?? 2, 10000)) }); }}><option value="">—</option>{overview.sheets.map(sheet => <option key={sheet.name} value={sheet.name} disabled={!sheet.selectable}>{sheet.name}</option>)}</select></label>
      <label className="presales-field">{c.question}<input value={mapping.questionColumn} maxLength={2} onChange={event => update({ questionColumn: event.target.value.toUpperCase() })} /></label>
      <label className="presales-field">{c.answer}<input value={mapping.answerColumn} maxLength={2} onChange={event => update({ answerColumn: event.target.value.toUpperCase() })} /></label>
      <label className="presales-field">{c.first}<input type="number" min={1} max={10000} value={mapping.firstRow} onChange={event => update({ firstRow: Number(event.target.value) })} /></label>
      <label className="presales-field">{c.last}<input type="number" min={1} max={10000} value={mapping.lastRow} onChange={event => update({ lastRow: Number(event.target.value) })} /></label>
    </div>
      {selectedSheet && <details><summary>{c.sample}</summary><div className="presales-workbook-preview">{selectedSheet.sample.map((row, index) => <p key={index}>{Object.entries(row).map(([column, text]) => <span key={column}><strong>{column}{index + 1}</strong> {text} </span>)}</p>)}</div></details>}
      <button type="button" onClick={() => void verify()}>{c.preview}</button>
    </>}
    {loading && <p role="status">{c.loading}</p>}{error && <p role="alert" className="presales-error">{error}</p>}
    {checked && <><p role="status">{c.count.replace("{count}", String(checked.questions.length))}</p><div className="presales-workbook-preview"><table><thead><tr><th>{c.question}</th><th>{c.answer}</th><th>{c.excel}</th></tr></thead><tbody>{checked.questions.map(question => <tr key={question.row}><td>{question.questionCell}</td><td>{question.answerCell}</td><td>{question.text}</td></tr>)}</tbody></table></div>
      <label className="presales-confirm"><input type="checkbox" checked={confirmed} onChange={event => { setConfirmed(event.target.checked); onChange(event.target.checked && upload ? { ...upload, mapping, confirmedSha256: checked.sha256 } : null); }} />{c.confirm}</label>
    </>}
  </fieldset>;
}
