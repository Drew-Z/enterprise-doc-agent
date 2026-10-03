import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { type PresalesRow, reviewInputSchema } from "./api";
import { ResponseRow } from "./ResponseRow";

const draft = {
  status: "conditional" as const, answer: "核对配置和验收。", conditions: ["需完成配置。", "需确认验收。"], missingInformation: [],
  prerequisites: [
    { condition: "已采购模块。", state: "met" as const, citationIndexes: [0] },
    { condition: "需完成配置。", state: "unmet" as const, citationIndexes: [0] },
    { condition: "需确认验收。", state: "unknown" as const, citationIndexes: [1] },
  ],
  citations: ["已采购，配置未完成。", "验收状态未登记。"].map((excerpt, index) => ({
    chunkId: `10000000-0000-4000-8000-00000000000${index + 1}`, documentVersionId: "20000000-0000-4000-8000-000000000001",
    excerpt, filename: `contract-${index + 1}.txt`, pageNumber: index + 1, heading: "条款", startOffset: 0, endOffset: excerpt.length,
  })), retrieval: [],
};
function fixture(): PresalesRow {
  return { id: "30000000-0000-4000-8000-000000000001", requirement: { key: "R1", text: "核对前提", sourceLocation: "" },
    revision: 1, state: "drafted", draft: structuredClone(draft), review: null, reviewHistory: [], attempts: [] };
}
afterEach(() => { cleanup(); localStorage.clear(); });

it("keeps three states and the evidence for each prerequisite, and requires a correction note", () => {
  const row = fixture(); const onReview = vi.fn();
  render(<ResponseRow row={row} busy={false} generating={false} onGenerate={vi.fn()} onReview={onReview} />);
  fireEvent.click(screen.getByRole("button", { name: "R1 Evidence and review" }));
  const assessment = screen.getByRole("list", { name: "Prerequisites" });
  expect(within(assessment).getByText("Met")).toBeInTheDocument();
  expect(within(assessment).getByText("Not met")).toBeInTheDocument();
  const unknown = within(assessment).getByText("Needs confirmation").closest("li")!;
  expect(within(unknown).getByText("验收状态未登记。")).toBeInTheDocument();
  expect(within(unknown).queryByText("已采购，配置未完成。")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Prerequisite state 2"), { target: { value: "met" } });
  fireEvent.click(screen.getByRole("button", { name: "Save review" }));
  expect(onReview).not.toHaveBeenCalled();
  expect(screen.getByRole("alert")).toHaveTextContent("Explain the changed prerequisite state in the review note.");
  fireEvent.change(screen.getByLabelText("Review note"), { target: { value: "核查配置已完成，验收仍待确认。" } });
  fireEvent.click(screen.getByRole("button", { name: "Save review" }));
  expect(onReview).toHaveBeenCalledOnce();
  const saved = reviewInputSchema.parse(onReview.mock.calls[0][0]);
  expect(saved.conditions).toEqual(["需确认验收。"]);
  expect(saved.prerequisites!.map(item => item.state)).toEqual(["met", "met", "unknown"]);
  expect(saved.prerequisites![2].citationIndexes).toEqual([1]);
  expect(row.draft!.prerequisites![1].state).toBe("unmet");
  expect(reviewInputSchema.safeParse({ ...saved, status: "supported" }).success).toBe(false);
});

it("shows legacy state as unrecorded and still permits text review", () => {
  const row = fixture(); row.draft!.prerequisites = null; const onReview = vi.fn();
  render(<ResponseRow row={row} busy={false} generating={false} onGenerate={vi.fn()} onReview={onReview} />);
  fireEvent.click(screen.getByRole("button", { name: "R1 Evidence and review" }));
  expect(screen.getByText("Prerequisite states were not recorded for this response.")).toBeInTheDocument();
  expect(screen.queryByLabelText("Prerequisite state 1")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Response conditions"), { target: { value: "需核对旧资料。" } });
  fireEvent.click(screen.getByRole("button", { name: "Save review" }));
  expect(onReview.mock.calls[0][0]).toMatchObject({ prerequisites: null, conditions: ["需核对旧资料。"] });
});
