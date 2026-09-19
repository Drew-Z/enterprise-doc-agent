import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { App } from "./App";
import { setLocale } from "./i18n";
import { tenantUsage, usageActor, usageTenantA } from "./test/tenantUsage";
import { createUploadTokenStore } from "./upload/persistence";

function pathOf(input: RequestInfo | URL) { return typeof input === "string" ? input : input instanceof URL ? input.href : input.url; }
function json(value: unknown, status = 200) { return Promise.resolve(new Response(JSON.stringify(value), { status })); }
function mockApi(role: "owner" | "member" = "owner", sessionStatus = 200) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(input => {
    const path = pathOf(input);
    if (path.endsWith("/health/ready")) return json({ status: "ready", checks: { database: { status: "up" }, redis: { status: "up" }, object_store: { status: "up" } } });
    if (path === "/api/session") return json(sessionStatus === 200 ? {
      tenantId: usageTenantA, actorId: usageActor, role,
      capabilities: { documentRead: true, documentWrite: true, agentRunCreate: true, auditRead: true, auditExport: true, approvalDecide: true },
    } : { error: { code: "session_unavailable", message: "Session unavailable", requestId: "req-session" } }, sessionStatus);
    if (path === "/api/session/logout") return json({ revoked: true, alreadyRevoked: false, revokedAt: "2026-09-15T08:00:00Z" });
    if (path === "/api/tenant-usage") return json(tenantUsage());
    throw new Error("Unexpected HTTP boundary: " + path);
  });
}
function mount() {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}><App /></QueryClientProvider>);
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear(); setLocale("en");
  window.history.replaceState(null, "", "/#/overview");
  createUploadTokenStore(sessionStorage).save("token-a");
});
afterEach(() => {
  cleanup(); vi.restoreAllMocks(); sessionStorage.clear(); localStorage.clear();
  window.history.replaceState(null, "", "/#/overview");
});

it("lets the owner open usage from navigation and clears it at sign-out", async () => {
  mockApi(); mount();
  fireEvent.click(await screen.findByRole("button", { name: "Enterprise usage" }));
  expect(window.location.hash).toBe("#/usage");
  expect(await screen.findByText("team-pilot")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
  await waitFor(() => expect(createUploadTokenStore(sessionStorage).load()).toBeNull());
  expect(await screen.findByText("Sign in and select an enterprise to view usage.")).toBeInTheDocument();
  expect(screen.queryByText("team-pilot")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Enterprise usage" })).not.toBeInTheDocument();
});

it("rejects direct usage navigation by a member without fetching any usage", async () => {
  window.history.replaceState(null, "", "/#/usage");
  const fetcher = mockApi("member"); mount();
  expect(await screen.findByText("Only enterprise administrators can view usage.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Enterprise usage" })).not.toBeInTheDocument();
  expect(fetcher.mock.calls.some(([input]) => pathOf(input) === "/api/tenant-usage")).toBe(false);
});

it("does not load usage when enterprise access could not be confirmed", async () => {
  window.history.replaceState(null, "", "/#/usage");
  const fetcher = mockApi("owner", 503); mount();
  expect(await screen.findByText("Enterprise access could not be confirmed. Refresh your session and try again.")).toBeInTheDocument();
  expect(fetcher.mock.calls.some(([input]) => pathOf(input) === "/api/tenant-usage")).toBe(false);
});
