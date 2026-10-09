import { useState, type FormEvent, type ReactNode } from "react";
import { useLocale } from "../i18n";
import { generationActive, manualResponseSchema, prerequisiteConditions, responseStatus, reviewInputSchema, reviewCitations, type Packet, type ManualResponseInput, type Evidence, type PrerequisiteAssessment, type PresalesRow, type ReviewInput, type ResponseStatus } from "./api";
import { presalesCopy, prerequisiteLabel, statusLabel } from "./copy";
import { PrerequisiteEditor, type AssessmentEdit } from "./PrerequisiteEditor";
import { EvidencePicker, type EvidenceReader } from "./EvidencePicker";

function citationKey(c: Evidence): string { return JSON.stringify([c.chunkId, c.documentVersionId, c.excerpt]); }
function remapPrerequisites(items: PrerequisiteAssessment[], from: Evidence[], to: Evidence[]): PrerequisiteAssessment[] {
  return items.map(item => ({ ...item, citationIndexes: item.citationIndexes.map(index => to.findIndex(c => citationKey(c) === citationKey(from[index]))).filter(index => index >= 0).sort((a, b) => a - b) }));
}

function ReviewOrigins({ review, original }: { review: PresalesRow["review"]; original: PrerequisiteAssessment[] }) {
  const c = presalesCopy(useLocale()); const changes = review?.prerequisiteChanges;
  if (!changes) return null;
  return <section className="presales-review-origins"><h4>{c.prerequisiteChanges}</h4><ul>
    {changes.origins.map((origin, i) => <li key={i}>{c.effectiveItem} {i + 1} ← {origin === null ? c.humanAdded : `${c.originalItem} ${origin + 1}`}</li>)}
    {changes.excludedIndexes.map(index => <li key={`excluded-${index}`}>{c.excludedOriginal} {index + 1}: {original[index].condition}</li>)}
  </ul><p>{review?.note}</p></section>;
}

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

export function ReviewEditor({ row, busy, onSave, onManual, manualEvidence = [], sources, readEvidence }: { row: PresalesRow; busy: boolean; onSave?: (payload: ReviewInput) => void; onManual?: (payload: ManualResponseInput) => void; manualEvidence?: Evidence[]; sources?: Packet["sources"]; readEvidence?: EvidenceReader }) {
  const locale = useLocale(); const c = presalesCopy(locale);
  const initial = row.review ?? row.draft;
  const [status, setStatus] = useState<ResponseStatus>(initial?.status ?? "insufficient_evidence");
  const [answer, setAnswer] = useState(initial?.answer ?? "");
  const [conditions, setConditions] = useState(initial?.conditions.join("\n") ?? "");
  const [assessment, setAssessment] = useState<AssessmentEdit>({ items: initial?.prerequisites ?? (onManual ? [] : null), origins: row.review?.prerequisiteChanges?.origins ?? initial?.prerequisites?.map((_, index) => index) ?? [] });
  const prerequisites = assessment.items;
  const original = row.draft?.prerequisites ?? [];
  const changes = prerequisites === null ? null : { origins: assessment.origins, excludedIndexes: original.map((_, index) => index).filter(index => !assessment.origins.includes(index)) };
  const [missing, setMissing] = useState(initial?.missingInformation.join("\n") ?? "");
  const [note, setNote] = useState(row.review?.note ?? "");
  const [error, setError] = useState("");
  const [selected, setSelected] = useState(() => reviewCitations(row.draft, row.review));
  const citations = onManual ? manualEvidence : selected;
  const selectEvidence = (next: Evidence[]) => {
    setAssessment(value => ({ ...value, items: value.items === null ? null : remapPrerequisites(value.items, citations, next) }));
    setSelected(next);
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const proposed = prerequisites?.map(item => ({ ...item, condition: item.condition.trim() })) ?? null;
    const content = { expectedRevision: row.revision, status, answer: answer.trim(), conditions: proposed === null ? conditions.split("\n").map(s => s.trim()).filter(Boolean) : prerequisiteConditions(proposed), prerequisites: proposed, missingInformation: missing.split("\n").map(s => s.trim()).filter(Boolean), note: note.trim() };
    if (onManual) {
      const parsed = manualResponseSchema.safeParse({ ...content, citations: manualEvidence.map(({ chunkId, documentVersionId, excerpt }) => ({ chunkId, documentVersionId, excerpt })) });
      if (!parsed.success) { setError(c.manualInvalid); return; }
      setError(""); onManual(parsed.data); return;
    }
    const originalMapping = { origins: original.map((_, index) => index), excludedIndexes: [] };
    const stateChanged = JSON.stringify(proposed) !== JSON.stringify(row.draft?.prerequisites ?? null) || JSON.stringify(proposed) !== JSON.stringify(initial?.prerequisites ?? null)
      || (changes !== null && (JSON.stringify(changes) !== JSON.stringify(originalMapping) || JSON.stringify(changes) !== JSON.stringify(row.review?.prerequisiteChanges ?? originalMapping)));
    if (stateChanged && !note.trim()) { setError(c.prerequisiteNoteRequired); return; }
    const citationChanged = [row.draft?.citations ?? [], reviewCitations(row.draft, row.review)].some(previous => JSON.stringify(previous.map(citationKey)) !== JSON.stringify(citations.map(citationKey)));
    if (citationChanged && !note.trim()) { setError(c.evidenceNoteRequired); return; }
    const parsed = reviewInputSchema.safeParse({ ...content, prerequisiteChanges: changes, citations: citations.map(({ chunkId, documentVersionId, excerpt }) => ({ chunkId, documentVersionId, excerpt })) });
    if (!parsed.success) { setError(c.reviewInvalid); return; }
    setError(""); onSave?.(parsed.data);
  };
  return <form onSubmit={submit} className="presales-review-form"><h4>{onManual ? c.manual : c.reviewTitle}</h4><p className="presales-hint">{onManual || row.manualAuthorship ? c.manualHelp : c.reviewHelp}</p>
    <label className="presales-field">{c.status}<select aria-label={c.status} value={status} onChange={e => setStatus(responseStatus.parse(e.target.value))} disabled={busy}>{responseStatus.options.map(s => <option key={s} value={s}>{statusLabel(s, locale)}</option>)}</select></label>
    <label className="presales-field">{c.response}<textarea value={answer} onChange={e => setAnswer(e.target.value)} rows={4} maxLength={4000} required disabled={busy} /></label>
    {!onManual && sources && readEvidence && <details className="presales-evidence-picker"><summary role="button">{c.editEvidence}</summary><EvidencePicker sources={sources} busy={busy} readEvidence={readEvidence} selected={citations} onChange={selectEvidence} /></details>}
    <PrerequisiteEditor value={assessment} original={remapPrerequisites(original, row.draft?.citations ?? [], citations)} citations={citations} busy={busy} onChange={setAssessment} />
    <div className="presales-two-fields">{prerequisites === null && <label className="presales-field">{c.conditions}<textarea value={conditions} onChange={e => setConditions(e.target.value)} rows={3} maxLength={12000} disabled={busy} /></label>}<label className="presales-field">{c.missing}<textarea value={missing} onChange={e => setMissing(e.target.value)} rows={3} maxLength={12000} disabled={busy} /></label></div>
    <label className="presales-field">{onManual ? c.manualNote : c.note}<input value={note} onChange={e => setNote(e.target.value)} maxLength={1000} disabled={busy} required={Boolean(onManual)} /></label>
    {error && <p role="alert" className="presales-error">{error}</p>}
    <button className="presales-primary" type="submit" disabled={busy}>{onManual ? c.manualSave : c.saveReview}</button>
  </form>;
}

export function ResponseRow({ row, busy, generating, onGenerate, onReview, manualEditor, sources, readEvidence }: { row: PresalesRow; busy: boolean; generating: boolean; onGenerate: () => void; onReview: (payload: ReviewInput) => void; manualEditor?: ReactNode; sources?: Packet["sources"]; readEvidence?: EvidenceReader }) {
  const locale = useLocale(); const c = presalesCopy(locale); const effective = row.review ?? row.draft;
  const citations = reviewCitations(row.draft, row.review);
  const lastAttempt = row.attempts.at(-1);
  return <article className="presales-row" aria-label={row.requirement.key}>
    <header className="presales-row-header"><span className="presales-row-number">{row.requirement.key}</span><div><h3>{row.requirement.text}</h3>{row.requirement.sourceLocation && <p className="presales-hint">{c.location}: {row.requirement.sourceLocation}</p>}</div><span className={"presales-state " + (effective?.status ?? row.state)}>{effective ? statusLabel(effective.status, locale) : row.state === "failed" ? c.failed : row.state === "queued" ? c.queued : row.state === "recovering" ? c.recovering : row.state === "running" || generating ? c.generating : c.pending}</span></header>
    {effective && <p className="presales-answer">{effective.answer}</p>}
    {row.manualAuthorship && <p className="presales-hint">{c.manualOrigin} · {row.manualAuthorship.actorId} · {new Date(row.manualAuthorship.createdAt).toLocaleString()} · {row.manualAuthorship.note}</p>}
    <div className="presales-row-actions"><span className={row.review ? "presales-reviewed" : "presales-hint"}>{row.review ? c.reviewed : row.draft ? c.unreviewed : generationActive(row) ? c.waiting : ""}</span>{!row.draft && <button type="button" disabled={busy || generationActive(row) || row.attempts.length >= 3} onClick={onGenerate}>{generating ? c.generating : row.state === "failed" ? c.retry : c.generate}</button>}</div>
    {row.state === "failed" && <p role="alert" className="presales-error">{lastAttempt?.errorCode === "presales_execution_policy_unavailable" ? c.policyUnavailable : lastAttempt?.errorCode?.includes("timeout") ? c.modelTimeout : c.rowError}</p>}
    {row.state === "recovering" && <p role="status" className="presales-hint">{c.recoveryHelp}</p>}
    {row.state === "failed" && <p className="presales-hint">{c.failedAllowance} {row.attempts.length >= 3 ? c.attemptsExhausted : c.retryHelp}</p>}
    {lastAttempt && <p className="presales-hint">{row.attempts.length} / 3 {c.attempts}</p>}
    {row.manualAuthorship && lastAttempt && <p className="presales-hint">{c.manualFailureKept}: {lastAttempt.state} · {lastAttempt.errorCode}</p>}
    {!row.draft && !generationActive(row) && manualEditor}
    {lastAttempt?.executionPolicy && <p className="presales-execution-mode">{c.savedMode.replace("{mode}", lastAttempt.executionPolicy.mode === "deep" ? c.modeDeep : c.modeAuto)}</p>}
    {row.attempts.some(attempt => attempt.executionPolicy) && <details className="presales-generation-history"><summary>{c.generationHistory}</summary>{row.attempts.filter(attempt => attempt.executionPolicy).map(attempt => <p key={attempt.id}>{attempt.number}. {attempt.executionPolicy!.mode === "deep" ? c.modeDeep : c.modeAuto} · {c.executionLimit.replace("{seconds}", String(attempt.executionPolicy!.rowTimeoutSeconds))}<small>{attempt.executionPolicy!.routes.map(route => route.modelName).join(" / ")}</small></p>)}</details>}
    {row.draft && <details className="presales-row-details"><summary role="button" aria-label={`${row.requirement.key} ${c.rowDetail}`}>{c.rowDetail}</summary>
      {effective && <><PrerequisiteList items={effective.prerequisites} citations={citations} /><div className="presales-two-fields">{effective.prerequisites === null && <section><h4>{c.conditions}</h4>{effective.conditions.length ? <ul>{effective.conditions.map((item, i) => <li key={i}>{item}</li>)}</ul> : <p className="presales-hint">{c.none}</p>}</section>}<section><h4>{c.missing}</h4>{effective.missingInformation.length ? <ul>{effective.missingInformation.map((item, i) => <li key={i}>{item}</li>)}</ul> : <p className="presales-hint">{c.none}</p>}</section></div></>}
      <section className="presales-evidence"><h4>{c.evidence}</h4><p className="presales-hint">{row.review?.citations != null ? c.reviewEvidenceSaved : row.manualAuthorship ? c.manualEvidenceHelp : c.retrievalNotice}</p>{row.review?.citations == null && row.draft.retrieval.some(r => r.truncated) && <p className="presales-notice">{c.truncated}</p>}{citations.length ? citations.map((citation, index) => <EvidenceFigure key={index} citation={citation} index={index} />) : <p>{c.noEvidence}</p>}</section>
      <ReviewEditor key={row.id + ":" + row.revision} row={row} busy={busy} onSave={onReview} sources={sources} readEvidence={readEvidence} />
      <ReviewOrigins review={row.review} original={row.draft.prerequisites ?? []} />
      {row.review && <details className="presales-original"><summary>{row.manualAuthorship ? c.manualOriginal : c.original}</summary><strong>{statusLabel(row.draft.status, locale)}</strong><p>{row.draft.answer}</p><PrerequisiteList items={row.draft.prerequisites} citations={row.draft.citations} />{row.draft.citations.map((citation, index) => <EvidenceFigure key={index} citation={citation} index={index} />)}{row.draft.prerequisites === null && <p>{row.draft.conditions.join("\n")}</p>}<p>{row.draft.missingInformation.join("\n")}</p></details>}
      {row.reviewHistory.length > 0 && <details className="presales-original"><summary>{c.history} ({row.reviewHistory.length})</summary>{row.reviewHistory.map(entry => <div key={entry.revision}><strong>{c.version} {entry.revision} · {statusLabel(entry.status, locale)}</strong><p>{entry.answer}</p><PrerequisiteList items={entry.prerequisites} citations={reviewCitations(row.draft, entry)} />{reviewCitations(row.draft, entry).map((citation, index) => <EvidenceFigure key={index} citation={citation} index={index} />)}<ReviewOrigins review={entry} original={row.draft!.prerequisites ?? []} />{entry.prerequisites === null && <p>{entry.conditions.join("\n")}</p>}<p>{entry.missingInformation.join("\n")}</p><p>{entry.note}</p><small>{c.reviewer}: {entry.actorId} · {new Date(entry.reviewedAt).toLocaleString()}</small></div>)}</details>}
    </details>}
  </article>;
}
