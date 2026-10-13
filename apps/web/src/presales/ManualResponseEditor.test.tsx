import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ManualResponseEditor } from "./ManualResponseEditor";
import { type PresalesRow, type Packet } from "./api";

afterEach(() => { cleanup(); localStorage.clear(); });
const row: PresalesRow = { id: "10000000-0000-4000-8000-000000000001", requirement: { key: "R1", text: "Retention", sourceLocation: "" }, revision: 0, state: "failed", draft: null, review: null, reviewHistory: [], attempts: [] };
const sources: Packet["sources"] = [{ versionId: "20000000-0000-4000-8000-000000000001", documentId: "30000000-0000-4000-8000-000000000001", generationId: "40000000-0000-4000-8000-000000000001", filename: "policy.txt", versionNumber: 1, latestVersionNumber: 1, contentSha256: "a".repeat(64), applicability: "Current procurement" }];
it("selects literal evidence and saves a human response without generation", async () => {
  const readEvidence = vi.fn().mockResolvedValue({ items: [{ chunkId: "50000000-0000-4000-8000-000000000001", documentVersionId: sources[0].versionId, excerpt: "Retention is 30 days.", filename: "policy.txt", pageNumber: 1, heading: null, startOffset: 0, endOffset: 21 }], nextOffset: null });
  const onSave = vi.fn();
  render(<ManualResponseEditor row={row} sources={sources} busy={false} readEvidence={readEvidence} onSave={onSave} />);
  fireEvent.click(screen.getByRole("button", { name: "Write manually" }));
  fireEvent.click(screen.getByRole("button", { name: "Search source text" }));
  fireEvent.click(await screen.findByRole("button", { name: "Select evidence 1" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm evidence and write" }));
  fireEvent.change(screen.getByLabelText("Assessment"), { target: { value: "supported" } });
  fireEvent.change(screen.getByLabelText("Response"), { target: { value: "Confirmed manually." } });
  fireEvent.change(screen.getByLabelText("Author note"), { target: { value: "Checked scope and source." } });
  fireEvent.click(screen.getByRole("button", { name: "Save human draft" }));
  expect(onSave).toHaveBeenCalledOnce();
  expect(onSave.mock.calls[0][0]).toMatchObject({ expectedRevision: 0, status: "supported", citations: [{ excerpt: "Retention is 30 days." }], prerequisites: [], note: "Checked scope and source." });
  expect(row.draft).toBeNull();
});
