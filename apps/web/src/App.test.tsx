import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { setLocale } from "./i18n";
import { createUploadTokenStore } from "./upload/persistence";

function renderApp() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>,
  );
}

function healthResponse(status: 200 | 503, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  sessionStorage.clear();
  localStorage.clear();
  setLocale("en");
  window.history.replaceState(null, "", "/#/overview");
});

describe("App readiness dashboard", () => {
  it("renders a stable loading state", () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => new Promise(() => undefined));

    renderApp();

    expect(screen.getByRole("status")).toHaveTextContent("Checking platform readiness");
    expect(screen.getByRole("button", { name: "Refresh readiness" })).toBeDisabled();
  });

  it("renders healthy components from a typed 200 response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      healthResponse(200, {
        status: "ready",
        checks: {
          database: { status: "up" },
          redis: { status: "up" },
          object_store: { status: "up" },
        },
      }),
    );

    renderApp();

    expect(await screen.findByText("Platform ready")).toBeInTheDocument();
    expect(screen.getAllByText("Operational")).toHaveLength(3);
  });

  it("renders degraded state from a typed 503 response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      healthResponse(503, {
        status: "not_ready",
        checks: {
          database: { status: "up" },
          redis: { status: "down" },
          object_store: { status: "timeout" },
        },
      }),
    );

    renderApp();

    expect(await screen.findByText("Platform degraded")).toBeInTheDocument();
    expect(screen.getByText("Unavailable")).toBeInTheDocument();
    expect(screen.getByText("Timed out")).toBeInTheDocument();
  });

  it.each([
    ["network error", () => Promise.reject(new TypeError("connection failed"))],
    [
      "invalid schema",
      () => Promise.resolve(healthResponse(200, { status: "ready", checks: {} })),
    ],
  ])("renders unreachable state for %s", async (_name, responseFactory) => {
    vi.spyOn(globalThis, "fetch").mockImplementation(responseFactory);

    renderApp();

    expect(await screen.findByText("API unreachable")).toBeInTheDocument();
    expect(screen.getByText("API unavailable")).toBeInTheDocument();
  });

  it("manually refreshes after a failure", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockRejectedValueOnce(new TypeError("connection failed"))
      .mockResolvedValueOnce(
        healthResponse(200, {
          status: "ready",
          checks: {
            database: { status: "up" },
            redis: { status: "up" },
            object_store: { status: "up" },
          },
        }),
      );

    renderApp();
    await screen.findByText("API unreachable");

    fireEvent.click(screen.getByRole("button", { name: "Refresh readiness" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("Platform ready")).toBeInTheDocument();
  });

  it("opens the workspace command search and filters actions", () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => new Promise(() => undefined));

    renderApp();

    fireEvent.click(screen.getByRole("button", { name: "Open Agent search" }));

    expect(screen.getByRole("dialog", { name: "Search the operations workspace" })).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "Workspace command search" }), {
      target: { value: "platform" },
    });

    expect(screen.getByRole("button", { name: /Review platform readiness/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Review documents/ })).not.toBeInTheDocument();
  });

  it("navigates product views with a shareable hash route", () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => new Promise(() => undefined));

    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Documents" }));

    expect(window.location.hash).toBe("#/documents");
    expect(screen.getByRole("heading", { level: 1, name: "Documents" })).toBeInTheDocument();
    expect(screen.getByText("Document inventory")).toBeInTheDocument();
  });

  it("follows browser history after navigating between product views", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => new Promise(() => undefined));

    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Documents" }));
    expect(screen.getByRole("heading", { level: 1, name: "Documents" })).toBeInTheDocument();

    window.history.back();

    await waitFor(() => {
      expect(window.location.hash).toBe("#/overview");
      expect(screen.getByRole("heading", { level: 1, name: "Knowledge operations" })).toBeInTheDocument();
    });
  });

  it("offers a token setup handoff from the unauthenticated Agent view", () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => new Promise(() => undefined));

    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Agent runs" }));

    expect(screen.getByRole("status")).toHaveTextContent("Connect your local session");
    fireEvent.click(screen.getByRole("button", { name: "Open Documents" }));
    expect(window.location.hash).toBe("#/documents");
  });

  it("provides the complete workspace navigation in the mobile drawer", () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => new Promise(() => undefined));

    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Open navigation" }));

    const drawer = screen.getByRole("complementary", { name: "Mobile navigation" });
    expect(drawer).toBeInTheDocument();
    expect(drawer).toHaveTextContent("Overview");
    expect(drawer).toHaveTextContent("Documents");
    expect(drawer).toHaveTextContent("Agent runs");
    expect(drawer).toHaveTextContent("Runtime health");
  });

  it("renders the local showcase snapshot without contacting the API", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    window.history.replaceState(null, "", "?showcase=1#/agent-runs");

    renderApp();

    expect((await screen.findAllByText("Showcase snapshot")).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByRole("status", { name: "Showcase mode" })).toHaveTextContent("Read-only showcase snapshot");
    expect(screen.getAllByText("Local fixture snapshot").length).toBeGreaterThan(0);
    expect(screen.getByText("Verified result")).toBeInTheDocument();
    expect(screen.getByText("Execution metadata")).toBeInTheDocument();
    expect(screen.getByText("local · reviewed4b · v1.0 · 2026-08-20")).toBeInTheDocument();
    expect(screen.getByText("800 total", { exact: false })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Demo only" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Download answer" })).toBeDisabled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("labels showcase runtime data as a local fixture", () => {
    vi.spyOn(globalThis, "fetch");
    window.history.replaceState(null, "", "?showcase=1#/runtime");

    renderApp();

    expect(screen.getByText("Local fixture")).toBeInTheDocument();
    expect(screen.queryByText(/Checked \d/)).not.toBeInTheDocument();
    expect(screen.getByText("Local fixture snapshot")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Release scope" })).toBeInTheDocument();
    expect(screen.getByText("External gate")).toBeInTheDocument();
    expect(screen.getByText("Deferred")).toBeInTheDocument();
  });

  it("uses read-only language in the showcase command search", () => {
    vi.spyOn(globalThis, "fetch");
    window.history.replaceState(null, "", "?showcase=1#/overview");

    renderApp();

    fireEvent.click(screen.getByRole("button", { name: "Open Agent search" }));

    expect(screen.getByRole("button", { name: /Review document inventory/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Inspect Agent run/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Start an Agent run/ })).not.toBeInTheDocument();
  });

  it("switches the workspace chrome to Chinese", () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => new Promise(() => undefined));

    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Language" }));

    expect(screen.getByRole("heading", { level: 1, name: "知识运营" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "文档" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "语言" })).toHaveTextContent("EN");
  });

  it("disables audit export for a member session", async () => {
    window.history.replaceState(null, "", "/#/overview");
    createUploadTokenStore(sessionStorage).save("member-token");
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = typeof input === "string"
        ? input
        : input instanceof URL
          ? input.toString()
          : input.url;
      if (url.endsWith("/health/ready")) {
        return Promise.resolve(healthResponse(200, {
          status: "ready",
          checks: {
            database: { status: "up" },
            redis: { status: "up" },
            object_store: { status: "up" },
          },
        }));
      }
      if (url.endsWith("/api/session")) {
        return Promise.resolve(new Response(JSON.stringify({
          tenantId: "00000000-0000-4000-8000-000000000001",
          actorId: "00000000-0000-4000-8000-000000000002",
          role: "member",
          capabilities: {
            documentRead: true,
            documentWrite: true,
            agentRunCreate: true,
            auditRead: true,
            auditExport: false,
            approvalDecide: false,
          },
        }), { status: 200, headers: { "Content-Type": "application/json" } }));
      }
      if (url.includes("/api/audit-events")) {
        return Promise.resolve(new Response(JSON.stringify({ items: [], nextCursor: null }), { status: 200, headers: { "Content-Type": "application/json" } }));
      }
      return Promise.resolve(new Response(JSON.stringify([]), { status: 200, headers: { "Content-Type": "application/json" } }));
    });

    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Audit log" }));

    expect(await screen.findByText("Member")).toBeInTheDocument();
    expect(screen.getByText(/Tenant 00000000/)).toBeInTheDocument();
    const exportButton = await screen.findByRole("button", { name: "Export audit CSV" });
    expect(exportButton).toBeDisabled();
    expect(exportButton).toHaveAttribute("title", "Only tenant owners can export audit events.");
  });

  it("revokes and clears the local session from the workspace chrome", async () => {
    createUploadTokenStore(sessionStorage).save("local-token");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      if (url.endsWith("/health/ready")) {
        return Promise.resolve(healthResponse(200, {
          status: "ready",
          checks: {
            database: { status: "up" },
            redis: { status: "up" },
            object_store: { status: "up" },
          },
        }));
      }
      if (url.endsWith("/api/session")) {
        return Promise.resolve(new Response(JSON.stringify({
          tenantId: "00000000-0000-4000-8000-000000000001",
          actorId: "00000000-0000-4000-8000-000000000002",
          role: "owner",
          capabilities: {
            documentRead: true,
            documentWrite: true,
            agentRunCreate: true,
            auditRead: true,
            auditExport: true,
            approvalDecide: true,
          },
        }), { status: 200, headers: { "Content-Type": "application/json" } }));
      }
      if (url.endsWith("/api/session/logout")) {
        expect(init?.method).toBe("POST");
        return Promise.resolve(new Response(JSON.stringify({
          revoked: true,
          alreadyRevoked: false,
          revokedAt: "2026-08-27T00:00:00Z",
        }), { status: 200, headers: { "Content-Type": "application/json" } }));
      }
      return Promise.resolve(new Response(JSON.stringify({}), { status: 200 }));
    });

    renderApp();
    expect(await screen.findByText("Tenant 00000000")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));

    await waitFor(() => expect(sessionStorage.getItem("enterprise-doc.upload-token.v1")).toBeNull());
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/session/logout",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("surfaces when server-side sign-out cannot be confirmed", async () => {
    createUploadTokenStore(sessionStorage).save("local-token");
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      if (url.endsWith("/health/ready")) {
        return Promise.resolve(healthResponse(200, {
          status: "ready",
          checks: {
            database: { status: "up" },
            redis: { status: "up" },
            object_store: { status: "up" },
          },
        }));
      }
      if (url.endsWith("/api/session")) {
        return Promise.resolve(new Response(JSON.stringify({
          tenantId: "00000000-0000-4000-8000-000000000001",
          actorId: "00000000-0000-4000-8000-000000000002",
          role: "owner",
          capabilities: {
            documentRead: true,
            documentWrite: true,
            agentRunCreate: true,
            auditRead: true,
            auditExport: true,
            approvalDecide: true,
          },
        }), { status: 200, headers: { "Content-Type": "application/json" } }));
      }
      if (url.endsWith("/api/session/logout")) {
        return Promise.resolve(new Response(null, { status: 503 }));
      }
      return Promise.resolve(new Response(JSON.stringify({}), { status: 200 }));
    });

    renderApp();
    expect(await screen.findByText("Tenant 00000000")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Server sign-out was not confirmed");
    expect(screen.getByRole("alert")).toHaveTextContent("browser session was cleared");
    expect(sessionStorage.getItem("enterprise-doc.upload-token.v1")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Dismiss sign-out warning" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("clears a revoked token and explains that the session must be reconnected", async () => {
    createUploadTokenStore(sessionStorage).save("revoked-token");
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      if (url.endsWith("/health/ready")) {
        return Promise.resolve(healthResponse(200, {
          status: "ready",
          checks: {
            database: { status: "up" },
            redis: { status: "up" },
            object_store: { status: "up" },
          },
        }));
      }
      if (url.endsWith("/api/session")) return Promise.resolve(new Response(null, { status: 401 }));
      return Promise.resolve(new Response(JSON.stringify({}), { status: 200 }));
    });

    renderApp();

    expect(await screen.findByRole("alert")).toHaveTextContent("Session expired or revoked");
    expect(screen.getByRole("alert")).toHaveTextContent("local token was removed");
    expect(sessionStorage.getItem("enterprise-doc.upload-token.v1")).toBeNull();
  });
});
