import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { WorkbookImport } from "./WorkbookImport";

afterEach(cleanup);
it("requires mapping preview and confirmation, and clears it on changes", async () => {
  const onChange = vi.fn();
  const preview = vi.fn((payload: { mapping?: unknown }) => Promise.resolve({ filename: "customer.xlsx", sha256: "a".repeat(64), sheets: [{ name: "Questions", maxRow: 14, maxColumn: 3, selectable: true, sample: [{ B: "Question", C: "Answer" }] }], questions: payload.mapping ? [{ row: 2, questionCell: "B2", answerCell: "C2", text: "Retention?" }] : [] }));
  render(<WorkbookImport busy={false} preview={preview} onChange={onChange} maxRows={120} />);
  fireEvent.change(screen.getByLabelText("Customer Excel file"), { target: { files: [new File(["test"], "customer.xlsx")] } });
  await screen.findByLabelText("Worksheet");
  expect(screen.queryByLabelText(/I checked/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Preview selected cells" }));
  await screen.findByText("Retention?");
  expect(onChange).toHaveBeenLastCalledWith(null);
  fireEvent.click(screen.getByLabelText(/I checked/));
  expect(onChange.mock.calls.at(-1)?.[0]).toMatchObject({ confirmedSha256: "a".repeat(64), mapping: { sheet: "Questions", questionColumn: "B", answerColumn: "C" } });
  fireEvent.change(screen.getByLabelText("Answer column"), { target: { value: "D" } });
  expect(onChange).toHaveBeenLastCalledWith(null);
  expect(screen.queryByLabelText(/I checked/)).not.toBeInTheDocument();
});
