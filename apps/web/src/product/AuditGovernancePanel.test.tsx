import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createUploadTokenStore } from "../upload/persistence";
import { AuditGovernancePanel } from "./AuditGovernancePanel";

const tenantId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const policy = { tenantId, retentionDays: 365, isEnabled: false, updatedBy: null };
const hold = {
  holdId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  tenantId,
  name: "Quarterly review",
  reason: "Preserve evidence.",
  resourceType: null,
  resourceId: null,
  startsAt: "2026-08-26T00:00:00+00:00",
  expiresAt: null,
  releasedAt: null,
  createdBy: tenantId,
  releasedBy: null,
};

function requestPath(input: RequestInfo | URL): string {
  return typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
}

function renderPanel(showcaseMode = false) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  render(<QueryClientProvider client={queryClient}><AuditGovernancePanel showcaseMode={showcaseMode} canManage /></QueryClientProvider>);
}

beforeEach(() => { sessionStorage.clear(); createUploadTokenStore(sessionStorage).save("local-token"); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("AuditGovernancePanel", () => {
  it("loads owner controls and saves the retention policy", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const path = requestPath(input);
      if ((init?.method ?? "GET") === "PUT") return Promise.resolve(new Response(JSON.stringify({ ...policy, retentionDays: 180, isEnabled: true }), { status: 200 }));
      if (path.endsWith("/retention-policy")) return Promise.resolve(new Response(JSON.stringify(policy), { status: 200 }));
      if (path.endsWith("/retention-preview")) return Promise.resolve(new Response(JSON.stringify({ cutoffAt: null, eligibleEventCount: 0, protectedEventCount: 0 }), { status: 200 }));
      if (path.includes("/retention-plan")) return Promise.resolve(new Response(JSON.stringify({ policy, cutoffAt: null, eligibleEventCount: 0, protectedEventCount: 0, eligibleEventIds: [], fingerprint: "a".repeat(64) }), { status: 200 }));
      if (path.includes("/retention-archives")) return Promise.resolve(new Response(JSON.stringify([]), { status: 200 }));
      return Promise.resolve(new Response(JSON.stringify([hold]), { status: 200 }));
    });
    renderPanel();

    expect(await screen.findByText("Audit governance")).toBeInTheDocument();
    const days = await screen.findByRole("spinbutton", { name: "Retention days" });
    fireEvent.change(days, { target: { value: "180" } });
    fireEvent.click(screen.getByRole("button", { name: "Save policy" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(true));
    expect(screen.getByText("Quarterly review")).toBeInTheDocument();
  });

  it("keeps showcase governance controls read-only and makes no API calls", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    renderPanel(true);
    expect(await screen.findByText("Read-only snapshot")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Save policy" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Create hold" })).toBeDisabled();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
