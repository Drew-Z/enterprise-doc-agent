import { useState, type FormEvent } from "react";
import { useLocale } from "../i18n";
import { generationActive, responseStatus, reviewInputSchema, type PresalesRow, type ReviewInput, type ResponseStatus } from "./api";
import { presalesCopy, statusLabel } from "./copy";

function ReviewEditor({ row, busy, onSave }: { row: PresalesRow; busy: boolean; onSave: (payload: ReviewInput) => void }) {
  const locale = useLocale(); const c = presalesCopy(locale);
  const initial = row.review ?? row.draft;
  const [status, setStatus] = useState<ResponseStatus>(initial?.status ?? "insufficient_evidence");
  const [answer, setAnswer] = useState(initial?.answer ?? "");
  const [conditions, setConditions] = useState(initial?.conditions.join("\n") ?? "");
  const [missing, setMissing] = useState(initial?.missingInformation.join("\n") ?? "");
  const [note, setNote] = useState(row.review?.note ?? "");
  const [error, setError] = useState("");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const parsed = reviewInputSchema.safeParse({ expectedRevision: row.revision, status, answer: answer.trim(), conditions: conditions.split("\n").map(s => s.trim()).filter(Boolean), missingInformation: missing.split("\n").map(s => s.trim()).filter(Boolean), note: note.trim() });
    if (!parsed.success || (status === "conditional" && !parsed.data.conditions.length) || (status === "insufficient_evidence" && !parsed.data.missingInformation.length) || (status === "supported" && parsed.data.conditions.length)) { setError(c.reviewInvalid); return; }
    setError(""); onSave(parsed.data);
  };
  return <form onSubmit={submit} className="presales-review-form"><h4>{c.reviewTitle}</h4><p className="presales-hint">{c.reviewHelp}</p>
    <label className="presales-field">{c.status}<select value={status} onChange={e => setStatus(responseStatus.parse(e.target.value))} disabled={busy}>{responseStatus.options.map(s => <option key={s} value={s}>{statusLabel(s, locale)}</option>)}</select></label>
    <label className="presales-field">{c.response}<textarea value={answer} onChange={e => setAnswer(e.target.value)} rows={4} maxLength={4000} required disabled={busy} /></label>
    <div className="presales-two-fields"><label className="presales-field">{c.conditions}<textarea value={conditions} onChange={e => setConditions(e.target.value)} rows={3} maxLength={12000} disabled={busy} /></label><label className="presales-field">{c.missing}<textarea value={missing} onChange={e => setMissing(e.target.value)} rows={3} maxLength={12000} disabled={busy} /></label></div>
    <label className="presales-field">{c.note}<input value={note} onChange={e => setNote(e.target.value)} maxLength={1000} disabled={busy} /></label>
    {error && <p role="alert" className="presales-error">{error}</p>}
    <button className="presales-primary" type="submit" disabled={busy}>{c.saveReview}</button>
  </form>;
}

export function ResponseRow({ row, busy, generating, onGenerate, onReview }: { row: PresalesRow; busy: boolean; generating: boolean; onGenerate: () => void; onReview: (payload: ReviewInput) => void }) {
  const locale = useLocale(); const c = presalesCopy(locale); const effective = row.review ?? row.draft;
  const lastAttempt = row.attempts.at(-1);
  return <article className="presales-row" aria-label={row.requirement.key}>
    <header className="presales-row-header"><span className="presales-row-number">{row.requirement.key}</span><div><h3>{row.requirement.text}</h3>{row.requirement.sourceLocation && <p className="presales-hint">{c.location}: {row.requirement.sourceLocation}</p>}</div><span className={"presales-state " + (effective?.status ?? row.state)}>{effective ? statusLabel(effective.status, locale) : row.state === "failed" ? c.failed : row.state === "queued" ? c.queued : row.state === "recovering" ? c.recovering : row.state === "running" || generating ? c.generating : c.pending}</span></header>
    {effective && <p className="presales-answer">{effective.answer}</p>}
    <div className="presales-row-actions"><span className={row.review ? "presales-reviewed" : "presales-hint"}>{row.review ? c.reviewed : row.draft ? c.unreviewed : generationActive(row) ? c.waiting : ""}</span>{!row.draft && <button type="button" disabled={busy || generationActive(row) || row.attempts.length >= 3} onClick={onGenerate}>{generating ? c.generating : row.state === "failed" ? c.retry : c.generate}</button>}</div>
    {row.state === "failed" && <p role="alert" className="presales-error">{lastAttempt?.errorCode?.includes("timeout") ? c.modelTimeout : c.rowError}</p>}
    {lastAttempt && <p className="presales-hint">{row.attempts.length} / 3 {c.attempts}</p>}
    {row.draft && <details className="presales-row-details"><summary role="button" aria-label={`${row.requirement.key} ${c.rowDetail}`}>{c.rowDetail}</summary>
      {effective && <div className="presales-two-fields"><section><h4>{c.conditions}</h4>{effective.conditions.length ? <ul>{effective.conditions.map((item, i) => <li key={i}>{item}</li>)}</ul> : <p className="presales-hint">{c.none}</p>}</section><section><h4>{c.missing}</h4>{effective.missingInformation.length ? <ul>{effective.missingInformation.map((item, i) => <li key={i}>{item}</li>)}</ul> : <p className="presales-hint">{c.none}</p>}</section></div>}
      <section className="presales-evidence"><h4>{c.evidence}</h4><p className="presales-hint">{c.retrievalNotice}</p>{row.draft.retrieval.some(r => r.truncated) && <p className="presales-notice">{c.truncated}</p>}{row.draft.citations.length ? row.draft.citations.map((citation, index) => <figure key={index}><blockquote>{citation.excerpt}</blockquote><figcaption><strong>{citation.filename}</strong>{citation.pageNumber && <span> · {c.page} {citation.pageNumber}</span>}{citation.heading && <span> · {citation.heading}</span>}<small>{c.offsets}: {citation.startOffset}–{citation.endOffset}</small></figcaption></figure>) : <p>{c.noEvidence}</p>}</section>
      <ReviewEditor key={row.id + ":" + row.revision} row={row} busy={busy} onSave={onReview} />
      {row.review && <details className="presales-original"><summary>{c.original}</summary><strong>{statusLabel(row.draft.status, locale)}</strong><p>{row.draft.answer}</p><p>{row.draft.conditions.join("\n")}</p><p>{row.draft.missingInformation.join("\n")}</p></details>}
      {row.reviewHistory.length > 0 && <details className="presales-original"><summary>{c.history} ({row.reviewHistory.length})</summary>{row.reviewHistory.map(entry => <div key={entry.revision}><strong>{statusLabel(entry.status, locale)}</strong><p>{entry.answer}</p><p>{entry.note}</p><small>{c.reviewer}: {entry.actorId} · {new Date(entry.reviewedAt).toLocaleString()}</small></div>)}</details>}
    </details>}
  </article>;
}
