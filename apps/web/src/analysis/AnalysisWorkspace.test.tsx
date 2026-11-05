import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AnalysisWorkspace } from "./AnalysisWorkspace";
import { blocksSchema, projectSchema, snapshotSchema } from "./api";

const id = "10000000-0000-4000-8000-000000000001";
const version = "10000000-0000-4000-8000-000000000002";
const timestamp = "2026-10-11T08:00:00Z";
const template = [{ id: "price", name: "Price", question: "What is the price and its unit?", valueType: "text" }];
const capabilities = { admissionEnabled: true, structuredUploadsEnabled: true, executionEnabled: false, maxSourceFiles: 10, maxObjects: 3, maxFields: 10, maxFileBytes: 10485760, template };
const inventory = { documentId: id, title: "Proposal", accessMode: "tenant", canManage: true, versionId: version, versionNumber: 1,
  generationId: id, filename: "quote.xlsx", mediaType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", sizeBytes: 600,
  versionStatus: "ready", ingestionStatus: "succeeded", ingestionStage: "ready", errorCode: null, createdAt: timestamp, updatedAt: timestamp };
const project = projectSchema.parse({ id, title: "Proposal review", revision: 1, createdAt: timestamp,
  spec: { schemaVersion: "analysis.spec.v2", commonRequirements: [], objects: [{ id: "supplier_a", name: "A", sourceVersionIds: [version] }], fields: template } });
const locator = { kind: "xlsx", page: null, line: null, part: null, paragraph: null, table: null, row: 2, column: 2,
  rowSpan: 1, columnSpan: 1, sheet: "Pricing", cell: "B2", mergedRange: null, hidden: true };
const source = { documentVersionId: version, generationId: id, objectId: "supplier_a", filename: "quote.xlsx", sourceSha256: "a".repeat(64), structureSha256: "b".repeat(64),
  parserVersion: 2, coverage: "partial", blockCount: 1, issues: [{ code: "formula_cache_missing", locator }] };
const snapshot = snapshotSchema.parse({ id, projectId: id, projectRevision: 1, sourceManifestHash: "c".repeat(64),
  payload: { schemaVersion: "analysis.sources.v2", title: project.title, spec: project.spec, sources: [source] }, createdAt: timestamp });
const blocks = blocksSchema.parse({ source, nextOffset: null, blocks: [{ index: 0, blockId: "d".repeat(64), kind: "table_cell", text: "=1200*12", originalText: "=1200*12",
  startOffset: 0, endOffset: 8, contentSha256: "e".repeat(64), locator, heading: null, formula: "=1200*12", cachedValue: null, numberFormat: "General" }] });

function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }); }
function mockApi(handler: (path: string, init: RequestInit) => Response) {
  return vi.spyOn(globalThis, "fetch").mockImplementation((input, init = {}) => Promise.resolve(handler(
    typeof input === "string" ? input : input instanceof URL ? input.href : input.url, init,
  )));
}
function mount() { return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}><AnalysisWorkspace token="test-token" contextKey="tenant:actor" /></QueryClientProvider>); }
beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("groups sources, freezes the saved revision, inspects formulas and prevents freezing unsaved edits", async () => {
  let created = false;
  let frozen = false;
  const fetch = mockApi((path, init) => {
    if (path.endsWith("/capabilities")) return json(capabilities);
    if (path === "/api/documents?limit=200") return json([inventory]);
    if (path === "/api/analysis/projects") {
      if (init.method === "POST") {
        if (typeof init.body !== "string") throw new Error("Expected JSON payload");
        expect(JSON.parse(init.body)).toEqual({ title: project.title, spec: project.spec });
        created = true;
        return json(project, 201);
      }
      return json(created ? [project] : []);
    }
    if (path.endsWith("/snapshots")) {
      if (init.method === "POST") {
        expect(new Headers(init.headers).get("If-Match")).toBe('"1"');
        frozen = true;
        return json(snapshot);
      }
      return json(frozen ? [snapshot] : []);
    }
    if (path.includes("/blocks?")) return json(blocks);
    if (path.endsWith("/runs")) return json([]);
    throw new Error(path);
  });
  mount();
  await screen.findByLabelText("Group: quote.xlsx");
  fireEvent.change(screen.getByLabelText("Project name"), { target: { value: project.title } });
  fireEvent.change(screen.getByLabelText("Group: quote.xlsx"), { target: { value: "supplier_a" } });
  fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Confirm source versions" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Confirm source versions" }));
  await screen.findByText("=1200*12");
  expect(screen.getByText(/Pricing!B2 · Hidden content/)).toBeInTheDocument();
  expect(screen.getByText("Formula has not been recalculated")).toBeInTheDocument();
  expect(screen.getByText(/No cached formula value is available/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Project name"), { target: { value: "Unsaved revision" } });
  expect(screen.getByRole("button", { name: "Confirm source versions" })).toBeDisabled();
  expect(fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(2);
});

it("keeps confirmed history readable when admission is disabled", async () => {
  mockApi(path => {
    if (path.endsWith("/capabilities")) return json({ ...capabilities, admissionEnabled: false, structuredUploadsEnabled: false });
    if (path === "/api/analysis/projects") return json([project]);
    if (path.endsWith("/snapshots")) return json([snapshot]);
    if (path.includes("/blocks?")) return json(blocks);
    if (path.endsWith("/runs")) return json([]);
    throw new Error(path);
  });
  mount();
  await screen.findByText("New projects are disabled. Existing confirmed sources remain available.");
  fireEvent.change(screen.getByLabelText("Select project"), { target: { value: id } });
  await screen.findByText("=1200*12");
  expect(screen.getByRole("button", { name: "Confirm source versions" })).toBeDisabled();
});

it("rejects malformed server contracts and exposes manual refresh", async () => {
  const fetch = mockApi(() => json({ status: "ok" }));
  mount();
  expect(await screen.findByRole("alert")).toHaveTextContent("Sources could not be loaded");
  expect(screen.queryByRole("button", { name: "Save draft" })).not.toBeInTheDocument();
  expect(fetch).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole("button", { name: "Refresh sources" }));
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(4));
});
