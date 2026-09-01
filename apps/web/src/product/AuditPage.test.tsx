import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createUploadTokenStore } from "../upload/persistence";
import { AuditPage } from "./AuditPage";
import { showcaseAuditEvents } from "./auditData";

function renderAudit(navigate = vi.fn(), showcaseMode = false) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  render(<QueryClientProvider client={queryClient}><AuditPage navigate={navigate} showcaseMode={showcaseMode} /></QueryClientProvider>);
  return navigate;
}

beforeEach(() => sessionStorage.clear());
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("AuditPage", () => {
  it("renders and filters the read-only showcase timeline without API calls", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const navigate = renderAudit(vi.fn(), true);

    expect(await screen.findByText("Local fixture snapshot")).toBeInTheDocument();
    expect((await screen.findAllByText("agent run / finished")).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Export audit CSV" })).toBeDisabled();
    expect(screen.getByText("Events in view").parentElement).toHaveTextContent(String(showcaseAuditEvents.length));
    fireEvent.change(screen.getByRole("searchbox", { name: "Search audit events" }), { target: { value: "information-security-policy" } });
    expect(screen.getAllByText("information-security-policy.pdf").length).toBeGreaterThan(0);
    fireEvent.change(screen.getByRole("searchbox", { name: "Search audit events" }), { target: { value: "agent run finished" } });
    expect(screen.getAllByText("agent run / finished").length).toBeGreaterThan(0);
    fireEvent.click(screen.getAllByRole("button", { name: "Open" })[0]);
    expect(navigate).toHaveBeenCalledWith("agent-runs");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("queries the tenant endpoint when a local token is available", async () => {
    createUploadTokenStore(sessionStorage).save("local-token");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ items: [showcaseAuditEvents[0]], nextCursor: null }), { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    renderAudit();

    expect(await screen.findByText("Current tenant")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/audit-events?limit=100",
      expect.objectContaining({ headers: { Accept: "application/json", Authorization: "Bearer local-token" } }),
    );
  });

  it("loads older pages with the server cursor", async () => {
    createUploadTokenStore(sessionStorage).save("local-token");
    const firstPage = { items: [showcaseAuditEvents[0]], nextCursor: "cursor-1" };
    const secondPage = { items: [showcaseAuditEvents[1]], nextCursor: null };
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify(firstPage), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify(secondPage), { status: 200, headers: { "Content-Type": "application/json" } }));
    renderAudit();

    await screen.findAllByText("information-security-policy.pdf");
    fireEvent.click(await screen.findByRole("button", { name: "Load older events" }));
    expect((await screen.findAllByText("Status: pending")).length).toBeGreaterThan(0);
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/audit-events?limit=100&cursor=cursor-1",
      expect.objectContaining({ headers: { Accept: "application/json", Authorization: "Bearer local-token" } }),
    );
  });
});
