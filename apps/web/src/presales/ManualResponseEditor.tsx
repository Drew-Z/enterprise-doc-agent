import { useState } from "react";
import { useLocale } from "../i18n";
import type { Evidence, ManualResponseInput, Packet, PresalesRow } from "./api";
import { EvidencePicker, type EvidenceReader } from "./EvidencePicker";
import { presalesCopy } from "./copy";
import { ReviewEditor } from "./ResponseRow";

export function ManualResponseEditor({ row, sources, busy, readEvidence, onSave }: {
  row: PresalesRow; sources: Packet["sources"]; busy: boolean;
  readEvidence: EvidenceReader;
  onSave: (payload: ManualResponseInput) => void;
}) {
  const c = presalesCopy(useLocale());
  const [selected, setSelected] = useState<Evidence[]>([]);
  const [confirmed, setConfirmed] = useState(false);
  return <details className="presales-row-details"><summary role="button">{c.manual}</summary>
    {!confirmed ? <EvidencePicker sources={sources} busy={busy} readEvidence={readEvidence} selected={selected} onChange={setSelected} onConfirm={() => setConfirmed(true)} /> : <>
      <section className="presales-evidence"><h4>{c.manualSelected} ({selected.length})</h4>{selected.map((item, index) => <figure key={index}><blockquote>{item.excerpt}</blockquote><figcaption>{index + 1}. {item.filename}</figcaption></figure>)}</section>
      <ReviewEditor row={row} busy={busy} onManual={onSave} manualEvidence={selected} />
    </>}
  </details>;
}
