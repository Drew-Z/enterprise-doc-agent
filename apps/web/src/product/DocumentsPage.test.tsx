import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createUploadRecoveryStore, createUploadTokenStore } from "../upload/persistence";
import { DocumentsPage } from "./DocumentsPage";

  const inventoryItem = {
  documentId: "33333333-3333-4333-8333-333333333333",
  title: "Security policy",
  accessMode: "restricted",
  canManage: true,
  versionId: "22222222-2222-4222-8222-222222222222",
  generationId: "44444444-4444-4444-8444-444444444444",
  versionNumber: 2,
  filename: "security-policy.pdf",
  mediaType: "application/pdf",
  sizeBytes: 524_288,
  versionStatus: "ready",
  ingestionStatus: "succeeded",
  ingestionStage: "ready",
  errorCode: null,
  createdAt: "2026-08-23T04:30:00Z",
  updatedAt: "2026-08-24T05:45:00Z",
};

function renderDocuments(navigate = vi.fn(), showcaseMode = false) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  render(
    <QueryClientProvider client={queryClient}>
      <DocumentsPage navigate={navigate} showcaseMode={showcaseMode} />
    </QueryClientProvider>,
  );
  return navigate;
}

beforeEach(() => {
  sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("DocumentsPage", () => {
  it("renders only API-backed authorized document versions and filters them", async () => {
    createUploadTokenStore(sessionStorage).save("local-token");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify([inventoryItem]), { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    const navigate = renderDocuments();

    const documentTable = await screen.findByRole("table");
    expect(within(documentTable).getByText("security-policy.pdf")).toBeInTheDocument();
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/documents?limit=200",
      expect.objectContaining({
        headers: { Accept: "application/json", Authorization: "Bearer local-token" },
      }),
    );
    expect(screen.getByText("Ready assets").parentElement).toHaveTextContent("1");
    expect(screen.getByText("Current tenant")).toBeInTheDocument();
    const expectedUpdatedDate = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(
      new Date(inventoryItem.updatedAt),
    );
    expect(within(documentTable).getByText(expectedUpdatedDate)).toBeInTheDocument();

    fireEvent.change(screen.getByRole("searchbox", { name: "Search documents" }), { target: { value: "missing" } });
    expect(screen.getAllByText("No documents match this search.")).toHaveLength(2);

    fireEvent.change(screen.getByRole("searchbox", { name: "Search documents" }), { target: { value: "security" } });
    fireEvent.click(within(documentTable).getByRole("button", { name: "Use in Agent" }));
    expect(navigate).toHaveBeenCalledWith("agent-runs");
  });

  it("filters the inventory by lifecycle status on desktop and mobile surfaces", async () => {
    createUploadTokenStore(sessionStorage).save("local-token");
    const processingItem = { ...inventoryItem, versionId: "55555555-5555-4555-8555-555555555555", versionStatus: "uploaded", ingestionStatus: "running", ingestionStage: "parse" };
    const failedItem = { ...inventoryItem, versionId: "66666666-6666-4666-8666-666666666666", versionStatus: "failed", ingestionStatus: "failed", ingestionStage: null, errorCode: "parse_failed" };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify([inventoryItem, processingItem, failedItem]), { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    renderDocuments();

    await screen.findByRole("table");
    const filterGroup = screen.getByRole("group", { name: "Filter document status" });
    fireEvent.click(within(filterGroup).getByRole("button", { name: "Processing" }));

    expect(screen.getAllByText("security-policy.pdf")).toHaveLength(2);
    expect(screen.queryByText("No documents match the current filters.")).not.toBeInTheDocument();
    expect(within(filterGroup).getByRole("button", { name: "Processing" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(within(filterGroup).getByRole("button", { name: "Failed" }));
    expect(screen.getAllByText("security-policy.pdf")).toHaveLength(2);

    fireEvent.click(within(filterGroup).getByRole("button", { name: "Ready" }));
    expect(screen.getAllByText("security-policy.pdf")).toHaveLength(2);
  });

  it("keeps local token entry inside the upload drawer", () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => new Promise(() => undefined));
    renderDocuments();

    expect(screen.queryByLabelText("Local API token")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open development access" }));
    expect(screen.getByRole("dialog", { name: "Upload document" })).toBeInTheDocument();
    expect(screen.getByLabelText("Local API token")).toBeInTheDocument();
  });

  it("reopens the upload drawer when a recoverable session exists", async () => {
    createUploadTokenStore(sessionStorage).save("local-token");
    createUploadRecoveryStore(sessionStorage).save({
      version: 1,
      sessionId: "77777777-7777-4777-8777-777777777777",
      filename: "recoverable.pdf",
      sizeBytes: 5_242_880,
      declaredSha256: "a".repeat(64),
      partSizeBytes: 5_242_880,
      expiresAt: "2026-08-27T00:00:00+00:00",
    });
    vi.spyOn(globalThis, "fetch").mockImplementation(() => new Promise(() => undefined));

    renderDocuments();

    expect(await screen.findByRole("dialog", { name: "Upload document" })).toBeInTheDocument();
    expect(await screen.findByText("Reselect original file")).toBeInTheDocument();
  });

  it("renders a read-only local showcase without requesting the API", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    renderDocuments(vi.fn(), true);

    const documentTable = await screen.findByRole("table");
    expect(within(documentTable).getByText("information-security-policy.pdf")).toBeInTheDocument();
    expect(screen.getByText("Showcase snapshot")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Demo only" })).toBeDisabled();
    expect(screen.getByRole("group", { name: "Filter document status" })).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("updates access mode and creates a role grant from the policy drawer", async () => {
    createUploadTokenStore(sessionStorage).save("local-token");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const path = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const method = init?.method ?? "GET";
      if (path === "/api/documents?limit=200") {
        return Promise.resolve(new Response(JSON.stringify([inventoryItem]), { status: 200 }));
      }
      if (path.endsWith("/access") && method === "GET") {
        return Promise.resolve(new Response(JSON.stringify({ documentId: inventoryItem.documentId, accessMode: "restricted", canManage: true }), { status: 200 }));
      }
      if (path.endsWith("/access") && method === "PUT") {
        return Promise.resolve(new Response(JSON.stringify({ documentId: inventoryItem.documentId, accessMode: "tenant", canManage: true }), { status: 200 }));
      }
      if (path.endsWith("/grants") && method === "GET") {
        return Promise.resolve(new Response(JSON.stringify([]), { status: 200 }));
      }
      if (path.endsWith("/grants") && method === "POST") {
        return Promise.resolve(new Response(JSON.stringify({
          grantId: "77777777-7777-4777-8777-777777777777",
          documentId: inventoryItem.documentId,
          granteeUserId: null,
          granteeRole: "member",
        }), { status: 201 }));
      }
      return Promise.resolve(new Response(null, { status: 500 }));
    });
    renderDocuments();

    await screen.findByRole("table");
    fireEvent.click(screen.getAllByRole("button", { name: "Manage access" })[0]);
    const drawer = await screen.findByRole("dialog", { name: "Access policy" });
    const modeControl = within(drawer).getByRole("group", { name: "Access" });
    fireEvent.click(within(modeControl).getByRole("button", { name: "Tenant" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      `/api/documents/${inventoryItem.documentId}/access`,
      expect.objectContaining({ method: "PUT", body: JSON.stringify({ accessMode: "tenant" }) }),
    ));

    const targetControl = within(drawer).getByRole("group", { name: "Grant target type" });
    fireEvent.click(within(targetControl).getByRole("button", { name: "Tenant role" }));
    fireEvent.click(within(drawer).getByRole("button", { name: "Add grant" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      `/api/documents/${inventoryItem.documentId}/grants`,
      expect.objectContaining({ method: "POST", body: JSON.stringify({ granteeRole: "member" }) }),
    ));
  });
});
