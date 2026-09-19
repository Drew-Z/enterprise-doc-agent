import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { setLocale } from "./i18n";
import { createUploadTokenStore } from "./upload/persistence";

const tenantA = "10000000-0000-4000-8000-000000000001";
const actorA = "10000000-0000-4000-8000-000000000002";
const tenantB = "20000000-0000-4000-8000-000000000001";
const actorB = "20000000-0000-4000-8000-000000000002";
const source = {
  documentId: "10000000-0000-4000-8000-000000000003", title: "A policy", accessMode: "restricted", canManage: true,
  versionId: "10000000-0000-4000-8000-000000000004", generationId: "10000000-0000-4000-8000-000000000005",
  versionNumber: 1, filename: "tenant-a.txt", mediaType: "text/plain", sizeBytes: 100,
  versionStatus: "ready", ingestionStatus: "succeeded", ingestionStage: "ready", errorCode: null,
  createdAt: "2026-09-12T10:00:00Z", updatedAt: "2026-09-12T10:00:00Z",
};
const sourceB = { ...source, documentId: "20000000-0000-4000-8000-000000000003", versionId: "20000000-0000-4000-8000-000000000004", generationId: "20000000-0000-4000-8000-000000000005", filename: "tenant-b.txt" };

function json(value: unknown) { return Promise.resolve(new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } })); }
function pathOf(input: RequestInfo | URL) { return typeof input === "string" ? input : input instanceof URL ? input.href : input.url; }
function mockApi() {
  return vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const path = pathOf(input);
    const second = new Headers(init?.headers).get("Authorization") === "Bearer token-b";
    if (path.endsWith("/health/ready")) return json({ status: "ready", checks: { database: { status: "up" }, redis: { status: "up" }, object_store: { status: "up" } } });
    if (path === "/api/session") return json({ tenantId: second ? tenantB : tenantA, actorId: second ? actorB : actorA, role: "owner", capabilities: { documentRead: true, documentWrite: true, agentRunCreate: true, auditRead: true, auditExport: true, approvalDecide: true } });
    if (path === "/api/session/logout") return json({ revoked: true, alreadyRevoked: false, revokedAt: "2026-09-12T10:00:00Z" });
    if (path === "/api/documents?limit=200") return json([second ? sourceB : source]);
    if (path === "/api/presales") return json([]);
    throw new Error("Unexpected HTTP boundary: " + path);
  });
}
function mount() {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}><App /></QueryClientProvider>);
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear(); setLocale("en");
  window.history.replaceState(null, "", "/#/documents");
  createUploadTokenStore(sessionStorage).save("token-a");
});
afterEach(() => {
  cleanup(); vi.restoreAllMocks(); sessionStorage.clear(); localStorage.clear();
  window.history.replaceState(null, "", "/#/overview");
});

describe("App presales source handoff", () => {
  it("consumes a source once and bypasses the old sheet without carrying it into later navigation", async () => {
    const oldId = "10000000-0000-4000-8000-000000000006";
    sessionStorage.setItem(`enterprise.presales.active:${tenantA}:${actorA}`, oldId);
    const fetch = mockApi(); mount();
    await screen.findByText("Tenant 10000000");
    fireEvent.click(within(await screen.findByRole("table")).getByRole("button", { name: "Start response sheet" }));
    expect(window.location.hash).toBe("#/presales");
    expect(await screen.findByRole("checkbox", { name: "tenant-a.txt Version 1" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /I confirm/ })).not.toBeChecked();
    expect(fetch.mock.calls.some(([input]) => pathOf(input) === "/api/presales/" + oldId)).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Documents" }));
    await screen.findByRole("table");
    fireEvent.click(screen.getByRole("button", { name: "Presales responses" }));
    expect(await screen.findByRole("checkbox", { name: "tenant-a.txt Version 1" })).not.toBeChecked();
  });

  it("clears the entry across sign-out and a different database identity", async () => {
    mockApi(); mount();
    await screen.findByText("Tenant 10000000");
    fireEvent.click(within(await screen.findByRole("table")).getByRole("button", { name: "Start response sheet" }));
    expect(await screen.findByRole("checkbox", { name: "tenant-a.txt Version 1" })).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(createUploadTokenStore(sessionStorage).load()).toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "Documents" }));
    fireEvent.click(await screen.findByRole("button", { name: "Open development access" }));
    fireEvent.change(screen.getByLabelText("Local API token"), { target: { value: "token-b" } });
    fireEvent.click(screen.getByRole("button", { name: "Save token" }));
    await screen.findByText("Tenant 20000000");
    fireEvent.click(screen.getByRole("button", { name: "Close upload" }));
    fireEvent.click(screen.getByRole("button", { name: "Presales responses" }));
    expect(await screen.findByRole("checkbox", { name: "tenant-b.txt Version 1" })).not.toBeChecked();
    expect(screen.queryByText("tenant-a.txt")).not.toBeInTheDocument();
    expect(screen.queryByText("Sources changed or are unavailable. Refresh the sources and create a new sheet.")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Documents" }));
    const inventory = await screen.findByRole("table");
    expect(within(inventory).getByText("tenant-b.txt")).toBeInTheDocument();
    expect(within(inventory).queryByText("tenant-a.txt")).not.toBeInTheDocument();
  });
});
