import { useLocale } from "../i18n";
import { prerequisiteState, type Evidence, type PrerequisiteAssessment } from "./api";
import { presalesCopy, prerequisiteLabel } from "./copy";

export type AssessmentEdit = { items: PrerequisiteAssessment[] | null; origins: (number | null)[] };

export function PrerequisiteEditor({ value, original, citations, busy, onChange }: {
  value: AssessmentEdit; original: PrerequisiteAssessment[]; citations: Evidence[];
  busy: boolean; onChange: (value: AssessmentEdit) => void;
}) {
  const locale = useLocale(); const c = presalesCopy(locale);
  const { items, origins } = value;
  if (items === null) return <button type="button" disabled={busy} onClick={() => onChange({ items: [], origins: [] })}>{c.startPrerequisites}</button>;
  const change = (index: number, patch: Partial<PrerequisiteAssessment>) => onChange({ ...value, items: items.map((item, i) => i === index ? { ...item, ...patch } : item) });
  const excluded = original.map((_, index) => index).filter(index => !origins.includes(index));
  return <fieldset className="presales-prerequisite-editor" disabled={busy}><legend>{c.prerequisites}</legend>
    <p className="presales-hint">{c.prerequisiteReviewHelp}</p>
    {items.map((item, index) => <fieldset key={index} className="presales-prerequisite-item"><legend>{index + 1}</legend>
      <label className="presales-field">{c.prerequisiteText} {index + 1}<textarea aria-label={`${c.prerequisiteText} ${index + 1}`} value={item.condition} rows={2} maxLength={1000} required onChange={e => change(index, { condition: e.target.value })} /></label>
      <div className="presales-two-fields">
        <label className="presales-field">{c.prerequisiteState} {index + 1}<select aria-label={`${c.prerequisiteState} ${index + 1}`} value={item.state} onChange={e => change(index, { state: prerequisiteState.parse(e.target.value) })}>{prerequisiteState.options.map(state => <option key={state} value={state}>{prerequisiteLabel(state, locale)}</option>)}</select></label>
        <label className="presales-field">{c.prerequisiteOrigin} {index + 1}<select aria-label={`${c.prerequisiteOrigin} ${index + 1}`} value={origins[index] ?? "new"} onChange={e => onChange({ ...value, origins: origins.map((origin, i) => i === index ? e.target.value === "new" ? null : Number(e.target.value) : origin) })}>
          <option value="new">{c.humanAdded}</option>{original.map((entry, i) => <option key={i} value={i}>{c.originalItem} {i + 1}: {entry.condition}</option>)}
        </select></label>
      </div>
      <fieldset className="presales-prerequisite-evidence"><legend>{c.prerequisiteEvidence}</legend>
        {citations.map((citation, i) => <label key={i}><input type="checkbox" aria-label={`${c.prerequisiteEvidenceChoice} ${index + 1}-${i + 1}`} checked={item.citationIndexes.includes(i)} onChange={e => change(index, { citationIndexes: e.target.checked ? [...item.citationIndexes, i].sort((a, b) => a - b) : item.citationIndexes.filter(value => value !== i) })} />
          <span>{c.evidence} {i + 1} · {citation.filename}<small>{citation.excerpt}</small></span></label>)}
        {!citations.length && <p className="presales-hint">{c.noEvidence}</p>}
      </fieldset>
      <div className="presales-prerequisite-actions">
        <button type="button" aria-label={`${c.splitPrerequisite} ${index + 1}`} disabled={items.length >= 12} onClick={() => onChange({ items: [...items.slice(0, index + 1), { ...item, citationIndexes: [...item.citationIndexes] }, ...items.slice(index + 1)], origins: [...origins.slice(0, index + 1), origins[index], ...origins.slice(index + 1)] })}>{c.splitPrerequisite}</button>
        <button type="button" aria-label={`${c.excludePrerequisite} ${index + 1}`} onClick={() => onChange({ items: items.filter((_, i) => i !== index), origins: origins.filter((_, i) => i !== index) })}>{c.excludePrerequisite}</button>
      </div>
    </fieldset>)}
    <button type="button" disabled={items.length >= 12 || !citations.length} onClick={() => onChange({ items: [...items, { condition: "", state: "unknown", citationIndexes: [] }], origins: [...origins, null] })}>{c.addPrerequisite}</button>
    {excluded.length > 0 && <section><h4>{c.excludedOriginals}</h4>{excluded.map(index => <div key={index} className="presales-excluded-item"><p>{c.originalItem} {index + 1}: {original[index].condition}</p>
      <button type="button" aria-label={`${c.restoreOriginal} ${index + 1}`} disabled={items.length >= 12} onClick={() => onChange({ items: [...items, original[index]], origins: [...origins, index] })}>{c.restoreOriginal}</button></div>)}</section>}
  </fieldset>;
}
