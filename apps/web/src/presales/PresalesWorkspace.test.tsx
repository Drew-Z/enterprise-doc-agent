import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { packetSchema, reviewInputSchema, type Packet } from "./api";
import { PresalesWorkspace } from "./PresalesWorkspace";

const packetId = "10000000-0000-4000-8000-000000000001";
const rowId = "10000000-0000-4000-8000-000000000002";
const versionId = "10000000-0000-4000-8000-000000000003";
const actorId = "10000000-0000-4000-8000-000000000004";
const storageKey = "presales-test:tenant-a:actor-a";
const timestamp = "2026-09-12T10:00:00Z";
const inventory = {
  documentId: "10000000-0000-4000-8000-000000000005", title: "Security policy",
  accessMode: "restricted", canManage: true, versionId,
  generationId: "10000000-0000-4000-8000-000000000006", versionNumber: 1,
  filename: "policy.txt", mediaType: "text/plain", sizeBytes: 200,
  versionStatus: "ready", ingestionStatus: "succeeded", ingestionStage: "ready", errorCode: null,
  createdAt: timestamp, updatedAt: timestamp,
};

function makePacket(drafted = false): Packet {
  return packetSchema.parse({
    id: packetId, title: "Customer retention response", createdAt: timestamp, rowCount: 1,
    staleSources: false,
    sources: [{ versionId, documentId: inventory.documentId, generationId: inventory.generationId,
      filename: inventory.filename, versionNumber: 1, latestVersionNumber: 1,
      contentSha256: "a".repeat(64), applicability: "Enterprise edition" }],
    rows: [{ id: rowId, requirement: { key: "R1", text: "Retention", sourceLocation: "Clause 3" },
      revision: drafted ? 1 : 0, state: drafted ? "drafted" : "pending",
      draft: drafted ? {
        status: "supported", answer: "The source states 30 days.", conditions: [], missingInformation: [],
        citations: [{ chunkId: "10000000-0000-4000-8000-000000000007", documentVersionId: versionId,
          excerpt: "Retention is 30 days.", filename: "policy.txt", pageNumber: 1,
          heading: "Retention", startOffset: 0, endOffset: 21 }],
        retrieval: [{ versionId, retrievedCount: 3, usedCount: 2, truncated: true }],
      } : null, review: null, reviewHistory: [], attempts: [],
    }],
  });
}

function attempt(state: "failed" | "succeeded", number = 1) {
  return {
    id: crypto.randomUUID(), number, state, errorCode: state === "failed" ? "presales_model_timeout" : null,
    modelProvider: "controlled-test", modelName: "test-only", providerRequestCount: 1,
    provenance: { promptVersion: "test.v1" }, usage: null, createdAt: timestamp,
    finishedAt: timestamp, deadlineAt: timestamp,
  };
}

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

function requestPath(input: RequestInfo | URL): string {
  return typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
}

function mockApi(current: () => Packet, mutate?: (path: string, init: RequestInit) => Response | Promise<Response>) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init = {}) => {
    const path = requestPath(input);
    if (path === "/api/documents?limit=200") return json([inventory, { ...inventory,
      versionId: "20000000-0000-4000-8000-000000000001", filename: "processing.txt",
      versionStatus: "uploaded", ingestionStatus: "running", ingestionStage: "parse" }]);
    if (path === "/api/presales" && (init.method ?? "GET") === "GET") {
      const value = current();
      return json([{ id: value.id, title: value.title, createdAt: value.createdAt, rowCount: 1, staleSources: false }]);
    }
    if (path === "/api/presales/" + packetId && (init.method ?? "GET") === "GET") return json(current());
    if (mutate) return await mutate(path, init);
    throw new Error("Unexpected HTTP boundary: " + path);
  });
}

function mount(client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }), contextKey = "tenant-a:actor-a", initialVersionId?: string) {
  return { client, ...render(<QueryClientProvider client={client}>
    <PresalesWorkspace key={contextKey} token="test-token" contextKey={contextKey}
      storageKey={storageKey + (contextKey === "tenant-a:actor-a" ? "" : ":other")}
      openDocuments={vi.fn()} initialVersionId={initialVersionId} />
  </QueryClientProvider>) };
}

async function fillCreationForm() {
  await screen.findByRole("checkbox", { name: "policy.txt Version 1" });
  fireEvent.change(screen.getByLabelText("Response sheet name"), { target: { value: "Customer retention response" } });
  fireEvent.click(screen.getByRole("checkbox", { name: "policy.txt Version 1" }));
  fireEvent.change(screen.getByLabelText("Source applicability"), { target: { value: "Enterprise edition" } });
  fireEvent.click(screen.getByRole("checkbox", { name: /I confirm/ }));
  fireEvent.change(screen.getByLabelText("2. Enter customer requirements"), { target: { value: "Retention\tClause 3" } });
  fireEvent.click(screen.getByRole("button", { name: "Save response sheet" }));
}

beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("PresalesWorkspace HTTP boundary", () => {
  it("starts a new sheet with the selected ready source instead of reopening the previous sheet", async () => {
    sessionStorage.setItem(storageKey, packetId);
    const fetch = mockApi(() => makePacket());
    mount(undefined, "tenant-a:actor-a", versionId);

    expect(await screen.findByRole("checkbox", { name: "policy.txt Version 1" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /I confirm/ })).not.toBeChecked();
    expect(screen.queryByRole("article", { name: "R1" })).not.toBeInTheDocument();
    expect(fetch.mock.calls.some(([input]) => requestPath(input) === "/api/presales/" + packetId)).toBe(false);
    expect(sessionStorage.getItem(storageKey)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "New response sheet" }));
    expect(screen.getByRole("checkbox", { name: "policy.txt Version 1" })).not.toBeChecked();
  });

  it("rejects an unavailable entry source and allows a fresh authorized selection", async () => {
    mockApi(() => makePacket());
    mount(undefined, "tenant-a:actor-a", "20000000-0000-4000-8000-000000000001");

    expect(await screen.findByRole("checkbox", { name: "policy.txt Version 1" })).not.toBeChecked();
    expect(screen.getByText("Sources changed or are unavailable. Refresh the sources and create a new sheet.")).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /processing.txt/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: "policy.txt Version 1" }));
    expect(screen.getByRole("checkbox", { name: "policy.txt Version 1" })).toBeChecked();
  });

  it("creates from authorized ready versions and recovers only the sheet id", async () => {
    const current = makePacket();
    const requests: RequestInit[] = [];
    mockApi(() => current, (path, init) => {
      expect(path).toBe("/api/presales"); requests.push(init); return json(current, 201);
    });
    const first = mount();
    await fillCreationForm();
    await screen.findByRole("article", { name: "R1" });
    expect(screen.queryByText("processing.txt")).not.toBeInTheDocument();
    const requestBody = requests[0].body;
    if (typeof requestBody !== "string") throw new Error("Expected a JSON request body.");
    const parsedBody: unknown = JSON.parse(requestBody);
    expect(parsedBody).toEqual({
      title: current.title, sources: [{ versionId, applicability: "Enterprise edition" }],
      requirements: [{ key: "R1", text: "Retention", sourceLocation: "Clause 3" }],
    });
    expect(new Headers(requests[0].headers).get("Authorization")).toBe("Bearer test-token");
    expect(new Headers(requests[0].headers).get("Idempotency-Key")).toMatch(/^[0-9a-f-]{36}$/);
    expect(sessionStorage.getItem(storageKey)).toBe(packetId);
    expect(JSON.stringify({ ...sessionStorage, ...localStorage })).not.toContain(current.title);
    first.unmount();
    mount();
    expect(await screen.findByRole("article", { name: "R1" })).toHaveTextContent("Retention");
  });

  it("reuses a key after a lost response and requires an explicit new retry after a saved failure", async () => {
    sessionStorage.setItem(storageKey, packetId);
    let current = makePacket();
    const keys: string[] = [];
    mockApi(() => current, (path, init) => {
      expect(path).toContain("/generate");
      keys.push(new Headers(init.headers).get("Idempotency-Key") ?? "");
      if (keys.length === 1) throw new TypeError("Connection lost");
      if (keys.length === 2) {
        current = makePacket(); current.rows[0].state = "failed";
        current.rows[0].attempts = [attempt("failed")];
      } else {
        current = makePacket(true); current.rows[0].attempts = [attempt("failed"), attempt("succeeded", 2)];
      }
      return json(current);
    });
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Generate response" }));
    await screen.findByText("Connection lost");
    expect(keys).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Generate response" }));
    await screen.findByText(/provider may still complete and charge/);
    expect(keys[1]).toBe(keys[0]);
    fireEvent.click(screen.getByRole("button", { name: "Retry this row" }));
    await screen.findByText("The source states 30 days.", { selector: "p" });
    expect(keys).toHaveLength(3);
    expect(keys[2]).not.toBe(keys[1]);
    expect(screen.getByRole("button", { name: "Export reviewed CSV" })).toBeDisabled();
  });

  it("keeps evidence and the original draft while an idempotent review enables export", async () => {
    sessionStorage.setItem(storageKey, packetId);
    let current = makePacket(true);
    const reviews: RequestInit[] = [];
    const exports: string[] = [];
    const createObjectURL = vi.fn(() => "blob:presales-test");
    vi.stubGlobal("URL", class extends URL {
      static createObjectURL = createObjectURL;
      static revokeObjectURL = vi.fn();
    });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    mockApi(() => current, (path, init) => {
      if (path.endsWith("/review")) {
        reviews.push(init);
        if (reviews.length === 1) throw new TypeError("Review response lost");
        if (typeof init.body !== "string") throw new Error("Expected a JSON review body.");
        const payload = reviewInputSchema.parse(JSON.parse(init.body));
        const { expectedRevision, ...text } = payload;
        expect(expectedRevision).toBe(1);
        const review = { ...text, revision: 2, actorId, reviewedAt: timestamp };
        current = makePacket(true); current.rows[0].revision = 2;
        current.rows[0].review = review; current.rows[0].reviewHistory = [review];
        return json(current);
      }
      exports.push(path);
      return new Response("\ufeffresponse,reviewed\n中文,yes", { headers: { "Content-Type": "text/csv; charset=utf-8" } });
    });
    mount();
    const row = await screen.findByRole("article", { name: "R1" });
    fireEvent.click(within(row).getByText("Evidence and review"));
    expect(within(row).getByText("Retention is 30 days.")).toBeInTheDocument();
    expect(within(row).getByText(/Some passages were shortened/)).toBeInTheDocument();
    fireEvent.change(within(row).getByLabelText("Response"), { target: { value: "Reviewed wording\nwith a second line" } });
    fireEvent.change(within(row).getByLabelText("Review note"), { target: { value: "Checked source" } });
    fireEvent.click(within(row).getByRole("button", { name: "Save review" }));
    await screen.findByText("Review response lost");
    fireEvent.click(within(row).getByRole("button", { name: "Save review" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Export reviewed CSV" })).toBeEnabled());
    expect(new Headers(reviews[0].headers).get("Idempotency-Key")).toBe(new Headers(reviews[1].headers).get("Idempotency-Key"));
    expect(within(row).getByText("The source states 30 days.")).toBeInTheDocument();
    expect(within(row).getByText("Review history (1)")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Export reviewed CSV" }));
    await waitFor(() => expect(exports).toEqual(["/api/presales/" + packetId + "/export?mode=reviewed"]));
    expect(createObjectURL).toHaveBeenCalledOnce();
  });

  it("reads the durable result after a proxy timeout without repeating generation", async () => {
    sessionStorage.setItem(storageKey, packetId);
    let current = makePacket();
    const calls: string[] = [];
    mockApi(() => current, path => {
      calls.push(path);
      current = makePacket(true);
      current.rows[0].attempts = [attempt("succeeded")];
      return new Response("<html>Gateway Timeout</html>", { status: 504 });
    });
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Generate response" }));
    await screen.findByText("The source states 30 days.", { selector: "p" });
    expect(calls).toHaveLength(1);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows the actual saved model failure after a lost HTTP response", async () => {
    sessionStorage.setItem(storageKey, packetId);
    const current = makePacket();
    const calls: string[] = [];
    mockApi(() => current, path => {
      calls.push(path);
      current.rows[0].state = "failed";
      current.rows[0].attempts = [attempt("failed")];
      return new Response("Gateway Timeout", { status: 504 });
    });
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Generate response" }));
    await screen.findByText(/provider may still complete and charge/);
    expect(screen.getByRole("button", { name: "Retry this row" })).toBeEnabled();
    expect(calls).toHaveLength(1);
    expect(screen.queryByText(/Request failed/)).not.toBeInTheDocument();
  });

  it("hides cached evidence immediately when export observes source revocation", async () => {
    sessionStorage.setItem(storageKey, packetId);
    mockApi(() => makePacket(true), () => json({ error: {
      code: "presales_source_unavailable", message: "Sources are no longer available.", requestId: "request-revoked",
    } }, 404));
    mount();
    await screen.findByText("The source states 30 days.", { selector: "p" });
    fireEvent.click(screen.getByRole("button", { name: "Export draft CSV" }));
    await screen.findByText(/request-revoked/);
    expect(screen.queryByText("The source states 30 days.")).not.toBeInTheDocument();
    expect(screen.queryByText("Retention is 30 days.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Back to new response sheet" })).toBeInTheDocument();
  });

  it("drops a late create response after a different session replaces the workspace", async () => {
    let resolveCreate!: (response: Response) => void;
    let signal: AbortSignal | null | undefined;
    const pending = new Promise<Response>(resolve => { resolveCreate = resolve; });
    const fetch = mockApi(() => makePacket(), async (_path, init) => { signal = init.signal; return pending; });
    const first = mount();
    await fillCreationForm();
    await waitFor(() => expect(signal).toBeDefined());
    first.unmount();
    mount(first.client, "tenant-b:actor-b");
    await screen.findByRole("checkbox", { name: "policy.txt Version 1" });
    const before = fetch.mock.calls.length;
    await act(async () => { resolveCreate(json(makePacket(), 201)); await pending; });
    expect(signal?.aborted).toBe(true);
    expect(first.client.getQueryData(["presales", "tenant-a:actor-a", packetId])).toBeUndefined();
    expect(sessionStorage.getItem(storageKey)).toBeNull();
    expect(fetch.mock.calls.length).toBe(before);
    expect(screen.queryByRole("article", { name: "R1" })).not.toBeInTheDocument();
  });

  it("rejects malformed HTTP output instead of displaying a model-provided review state", async () => {
    sessionStorage.setItem(storageKey, packetId);
    const current = makePacket(true);
    const forged = { ...current, rows: [{ ...current.rows[0], reviewState: "approved" }] };
    vi.spyOn(globalThis, "fetch").mockImplementation(input => Promise.resolve(requestPath(input).endsWith(packetId) ? json(forged) : json([])));
    mount();
    await screen.findByRole("alert");
    expect(screen.queryByText("The source states 30 days.")).not.toBeInTheDocument();
    expect(screen.queryByText("Reviewed")).not.toBeInTheDocument();
  });
});
