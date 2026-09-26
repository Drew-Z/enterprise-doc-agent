import { useState, type FormEvent } from "react";
import { useLocale } from "../i18n";
import { generationActive, prerequisiteConditions, prerequisiteState, responseStatus, reviewInputSchema, type Evidence, type PrerequisiteAssessment, type PresalesRow, type ReviewInput, type ResponseStatus } from "./api";
import { presalesCopy, prerequisiteLabel, statusLabel } from "./copy";

function EvidenceFigure({ citation, index }: { citation: Evidence; index: number }) {
  const c = presalesCopy(useLocale());
  return <figure><blockquote>{citation.excerpt}</blockquote><figcaption>
    <strong>{c.evidence} {index + 1} · {citation.filename}</strong>
    {citation.pageNumber && <span> · {c.page} {citation.pageNumber}</span>}
    {citation.heading && <span> · {citation.heading}</span>}
    <small>{c.offsets}: {citation.startOffset}–{citation.endOffset}</small>
  </figcaption></figure>;
}

function PrerequisiteList({ items, citations }: { items: PrerequisiteAssessment[] | null; citations: Evidence[] }) {
  const locale = useLocale(); const c = presalesCopy(locale);
  return <section className="presales-prerequisites"><h4>{c.prerequisites}</h4>
    {items === null ? <p className="presales-hint">{c.prerequisitesUnrecorded}</p> : !items.length ? <p className="presales-hint">{c.noPrerequisites}</p> :
      <ul aria-label={c.prerequisites}>{items.map((item, index) => <li key={index}>
        <strong className={"presales-prerequisite-state " + item.state}>{prerequisiteLabel(item.state, locale)}</strong> <span>{item.condition}</span>
        <details className="presales-evidence"><summary>{c.prerequisiteEvidence} ({item.citationIndexes.length})</summary>
          {item.citationIndexes.map(i => <EvidenceFigure key={i} citation={citations[i]} index={i} />)}
        </details>
      </li>)}</ul>}
  </section>;
}

function ReviewEditor({ row, busy, onSave }: { row: PresalesRow; busy: boolean; onSave: (payload: ReviewInput) => void }) {
  const locale = useLocale(); const c = presalesCopy(locale);
  const initial = row.review ?? row.draft;
  const [status, setStatus] = useState<ResponseStatus>(initial?.status ?? "insufficient_evidence");
  const [answer, setAnswer] = useState(initial?.answer ?? "");
  const [conditions, setConditions] = useState(initial?.conditions.join("\n") ?? "");
  const [prerequisites, setPrerequisites] = useState(initial?.prerequisites ?? null);
  const [missing, setMissing] = useState(initial?.missingInformation.join("\n") ?? "");
  const [note, setNote] = useState(row.review?.note ?? "");
  const [error, setError] = useState("");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const stateChanged = prerequisites?.some((item, index) => item.state !== row.draft?.prerequisites?.[index]?.state || item.state !== initial?.prerequisites?.[index]?.state);
    if (stateChanged && !note.trim()) { setError(c.prerequisiteNoteRequired); return; }
    const parsed = reviewInputSchema.safeParse({ expectedRevision: row.revision, status, answer: answer.trim(), conditions: prerequisites === null ? conditions.split("\n").map(s => s.trim()).filter(Boolean) : prerequisiteConditions(prerequisites), prerequisites, missingInformation: missing.split("\n").map(s => s.trim()).filter(Boolean), note: note.trim() });
    if (!parsed.success) { setError(c.reviewInvalid); return; }
    setError(""); onSave(parsed.data);
  };
  return <form onSubmit={submit} className="presales-review-form"><h4>{c.reviewTitle}</h4><p className="presales-hint">{c.reviewHelp}</p>
    <label className="presales-field">{c.status}<select value={status} onChange={e => setStatus(responseStatus.parse(e.target.value))} disabled={busy}>{responseStatus.options.map(s => <option key={s} value={s}>{statusLabel(s, locale)}</option>)}</select></label>
    <label className="presales-field">{c.response}<textarea value={answer} onChange={e => setAnswer(e.target.value)} rows={4} maxLength={4000} required disabled={busy} /></label>
    {prerequisites !== null && prerequisites.length > 0 && <fieldset className="presales-prerequisite-editor"><legend>{c.prerequisites}</legend><p className="presales-hint">{c.prerequisiteReviewHelp}</p>
      {prerequisites.map((item, index) => <label key={index} className="presales-field"><span>{index + 1}. {item.condition}</span>
        <select aria-label={`${c.prerequisiteState} ${index + 1}`} disabled={busy} value={item.state} onChange={event => {
          const state = prerequisiteState.parse(event.target.value);
          setPrerequisites(prerequisites.map((value, i) => i === index ? { ...value, state } : value));
        }}>{prerequisiteState.options.map(state => <option key={state} value={state}>{prerequisiteLabel(state, locale)}</option>)}</select>
      </label>)}
    </fieldset>}
    <div className="presales-two-fields">{prerequisites === null && <label className="presales-field">{c.conditions}<textarea value={conditions} onChange={e => setConditions(e.target.value)} rows={3} maxLength={12000} disabled={busy} /></label>}<label className="presales-field">{c.missing}<textarea value={missing} onChange={e => setMissing(e.target.value)} rows={3} maxLength={12000} disabled={busy} /></label></div>
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
      {effective && <><PrerequisiteList items={effective.prerequisites} citations={row.draft.citations} /><div className="presales-two-fields">{effective.prerequisites === null && <section><h4>{c.conditions}</h4>{effective.conditions.length ? <ul>{effective.conditions.map((item, i) => <li key={i}>{item}</li>)}</ul> : <p className="presales-hint">{c.none}</p>}</section>}<section><h4>{c.missing}</h4>{effective.missingInformation.length ? <ul>{effective.missingInformation.map((item, i) => <li key={i}>{item}</li>)}</ul> : <p className="presales-hint">{c.none}</p>}</section></div></>}
      <section className="presales-evidence"><h4>{c.evidence}</h4><p className="presales-hint">{c.retrievalNotice}</p>{row.draft.retrieval.some(r => r.truncated) && <p className="presales-notice">{c.truncated}</p>}{row.draft.citations.length ? row.draft.citations.map((citation, index) => <EvidenceFigure key={index} citation={citation} index={index} />) : <p>{c.noEvidence}</p>}</section>
      <ReviewEditor key={row.id + ":" + row.revision} row={row} busy={busy} onSave={onReview} />
      {row.review && <details className="presales-original"><summary>{c.original}</summary><strong>{statusLabel(row.draft.status, locale)}</strong><p>{row.draft.answer}</p><PrerequisiteList items={row.draft.prerequisites} citations={row.draft.citations} />{row.draft.prerequisites === null && <p>{row.draft.conditions.join("\n")}</p>}<p>{row.draft.missingInformation.join("\n")}</p></details>}
      {row.reviewHistory.length > 0 && <details className="presales-original"><summary>{c.history} ({row.reviewHistory.length})</summary>{row.reviewHistory.map(entry => <div key={entry.revision}><strong>{statusLabel(entry.status, locale)}</strong><p>{entry.answer}</p><PrerequisiteList items={entry.prerequisites} citations={row.draft!.citations} />{entry.prerequisites === null && <p>{entry.conditions.join("\n")}</p>}<p>{entry.missingInformation.join("\n")}</p><p>{entry.note}</p><small>{c.reviewer}: {entry.actorId} · {new Date(entry.reviewedAt).toLocaleString()}</small></div>)}</details>}
    </details>}
  </article>;
}
