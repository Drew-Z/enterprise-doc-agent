import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { BrowserSessionBoundary } from "./auth/BrowserSessionBoundary";
import type { AuthEntry } from "./auth/entry";
import { configureAuthentication, type Fetcher } from "./auth/transport";
import { setLocale } from "./i18n";

const tenantId = "30000000-0000-4000-8000-000000000001";
const actorId = "30000000-0000-4000-8000-000000000002";
const documentId = "30000000-0000-4000-8000-000000000003";
const versionId = "30000000-0000-4000-8000-000000000004";
const generationId = "30000000-0000-4000-8000-000000000005";
const membershipId = "30000000-0000-4000-8000-000000000006";
const packetId = "30000000-0000-4000-8000-000000000007";
const rowId = "30000000-0000-4000-8000-000000000008";
const invitationToken = "inv1_" + "x".repeat(43);
const contextVersion = "a".repeat(32) + ".2";
const csrfToken = "c".repeat(64);
const timestamp = "2026-09-14T10:00:00Z";

const targetTenant = { tenantId, actorId, name: "受邀企业", role: "member" as const };
const browserSession = {
  status: "authenticated" as const,
  email: "member@example.test",
  expiresAt: "2099-01-01T00:00:00Z",
  contextVersion: "a".repeat(32) + ".1",
  csrfToken: "b".repeat(64),
  currentTenant: null,
};
const readyDocument = {
  documentId,
  title: "安全策略",
  accessMode: "tenant" as const,
  canManage: false,
  versionId,
  versionNumber: 1,
  filename: "security-policy.txt",
  mediaType: "text/plain",
  sizeBytes: 200,
  versionStatus: "ready" as const,
  generationId,
  ingestionStatus: "succeeded" as const,
  ingestionStage: "ready" as const,
  errorCode: null,
  createdAt: timestamp,
  updatedAt: timestamp,
};

function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

function response(value: unknown, status = 200): Promise<Response> {
  return Promise.resolve(json(value, status));
}

function pathOf(input: RequestInfo | URL): string {
  return typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
}

function productSession() {
  return {
    tenantId,
    actorId,
    role: "member",
    capabilities: {
      documentRead: true,
      documentWrite: false,
      agentRunCreate: true,
      auditRead: true,
      auditExport: false,
      approvalDecide: false,
    },
  };
}

function packet() {
  return {
    id: packetId,
    title: "客户安全响应",
    createdAt: timestamp,
    rowCount: 1,
    staleSources: false,
    sources: [{
      versionId,
      documentId,
      generationId,
      filename: readyDocument.filename,
      versionNumber: 1,
      latestVersionNumber: 1,
      contentSha256: "a".repeat(64),
      applicability: "企业版部署",
    }],
    rows: [{
      id: rowId,
      requirement: { key: "R1", text: "保留期限", sourceLocation: "第 3 条" },
      revision: 0,
      state: "pending",
      draft: null,
      review: null,
      reviewHistory: [],
      attempts: [],
    }],
  };
}

function entry(withInvitation = true): AuthEntry {
  return {
    admissionToken: null,
    invitationToken: withInvitation ? invitationToken : null,
    admissionLink: false,
    invitationLink: withInvitation,
    signInFailed: false,
    releaseSecrets: () => undefined,
  };
}

function renderWorkspace(withInvitation = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0, refetchOnWindowFocus: false } } });
  return render(
    <QueryClientProvider client={client}>
      <BrowserSessionBoundary entry={entry(withInvitation)}>{session => <App browserSession={session} />}</BrowserSessionBoundary>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  configureAuthentication("browser");
  setLocale("en");
  sessionStorage.clear();
  localStorage.clear();
  window.history.replaceState(null, "", "/#/overview");
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  configureAuthentication("bearer");
  sessionStorage.clear();
  localStorage.clear();
  window.history.replaceState(null, "", "/#/overview");
});

describe("browser onboarding integration", () => {
  it("accepts an invitation, selects the enterprise, and creates a response sheet from a ready document", async () => {
    let accepted = false;
    let selected = false;
    const calls: Array<{ path: string; method: string; headers: Headers; body: unknown }> = [];
    const fetcher = vi.fn<Fetcher>((input, init = {}) => {
      const path = pathOf(input);
      const method = (init.method ?? "GET").toUpperCase();
      const headers = new Headers(init.headers);
      const body = typeof init.body === "string" ? JSON.parse(init.body) as unknown : null;
      calls.push({ path, method, headers, body });

      if (path === "/auth/session") return response({ ...browserSession, currentTenant: selected ? targetTenant : null });
      if (path === "/auth/tenants") return response(accepted ? [targetTenant] : []);
      if (path === "/auth/invitations/inspect") return response({ tenantName: targetTenant.name, expiresAt: browserSession.expiresAt, state: "pending" });
      if (path === "/auth/invitations/accept") {
        accepted = true;
        return response({ tenantId, tenantName: targetTenant.name, membershipId, replayed: false });
      }
      if (path === "/auth/tenant") {
        selected = true;
        expect(body).toEqual({ tenantId });
        return response({ ...browserSession, contextVersion, csrfToken, currentTenant: targetTenant });
      }
      if (path === "/health/ready") return response({ status: "ready", checks: { database: { status: "up" }, redis: { status: "up" }, object_store: { status: "up" } } });
      if (path === "/api/session") return response(productSession());
      if (path === "/api/documents?limit=200") return response([readyDocument]);
      if (path === "/api/presales" && method === "GET") return response([]);
      if (path === "/api/presales" && method === "POST") return response(packet(), 201);
      throw new Error(`Unexpected HTTP boundary: ${method} ${path}`);
    });
    vi.stubGlobal("fetch", fetcher);

    renderWorkspace();

    fireEvent.change(await screen.findByLabelText("Invitation code"), { target: { value: invitationToken } });
    fireEvent.click(screen.getByRole("button", { name: "Review invitation" }));
    expect(await screen.findByRole("heading", { name: targetTenant.name })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Confirm membership" }));
    expect(await screen.findByText("You have joined the enterprise. Select it to continue, or refresh the list.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: `Open ${targetTenant.name}` }));

    expect(await screen.findByRole("button", { name: "Documents" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Documents" }));
    const table = await screen.findByRole("table");
    expect(within(table).getByText(readyDocument.filename)).toBeInTheDocument();
    const start = within(table).getByRole("button", { name: "Start response sheet" });
    expect(start).toBeEnabled();
    fireEvent.click(start);

    expect(window.location.hash).toBe("#/presales");
    expect(await screen.findByRole("checkbox", { name: `${readyDocument.filename} Version 1` })).toBeChecked();
    fireEvent.change(screen.getByLabelText("Response sheet name"), { target: { value: "客户安全响应" } });
    fireEvent.change(screen.getByLabelText("Source applicability"), { target: { value: "企业版部署" } });
    fireEvent.click(screen.getByRole("checkbox", { name: /I confirm/ }));
    fireEvent.change(screen.getByLabelText("2. Enter customer requirements"), { target: { value: "保留期限\t第 3 条" } });
    fireEvent.click(screen.getByRole("button", { name: "Save response sheet" }));

    expect(await screen.findByText("客户安全响应")).toBeInTheDocument();
    const documentCall = calls.find(call => call.path === "/api/documents?limit=200");
    expect(documentCall?.headers.get("X-Session-Context")).toBe(contextVersion);
    expect(documentCall?.headers.get("Authorization")).toBeNull();
    const createCall = calls.find(call => call.path === "/api/presales" && call.method === "POST");
    expect(createCall?.headers.get("X-Session-Context")).toBe(contextVersion);
    expect(createCall?.headers.get("X-CSRF-Token")).toBe(csrfToken);
    expect(createCall?.headers.get("Authorization")).toBeNull();
    expect(createCall?.body).toEqual({
      title: "客户安全响应",
      sources: [{ versionId, applicability: "企业版部署" }],
      requirements: [{ key: "R1", text: "保留期限", sourceLocation: "第 3 条" }],
    });
    expect(calls.find(call => call.path === "/auth/invitations/accept")?.headers.get("X-Session-Context")).toBe(browserSession.contextVersion);
    expect(calls.find(call => call.path === "/auth/invitations/accept")?.headers.get("X-CSRF-Token")).toBe(browserSession.csrfToken);
  });

  it("does not render business pages or send business requests before enterprise selection", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn<Fetcher>((input) => {
      const path = pathOf(input);
      calls.push(path);
      if (path === "/auth/session") return response(browserSession);
      if (path === "/auth/tenants") return response([]);
      return response({ status: "ready", checks: { database: { status: "up" }, redis: { status: "up" }, object_store: { status: "up" } } });
    }));
    renderWorkspace(false);
    expect(await screen.findByRole("heading", { name: "Choose an enterprise" })).toBeInTheDocument();
    await waitFor(() => expect(calls.some(path => path.startsWith("/api/"))).toBe(false));
    expect(screen.queryByRole("heading", { name: "Documents" })).not.toBeInTheDocument();
  });

  it("retires the old browser credential before a delayed document response can cross an enterprise switch", async () => {
    const tenantA = { tenantId: "40000000-0000-4000-8000-000000000001", actorId: "40000000-0000-4000-8000-000000000002", name: "企业 A", role: "owner" as const };
    const tenantB = { tenantId: "40000000-0000-4000-8000-000000000003", actorId: "40000000-0000-4000-8000-000000000004", name: "企业 B", role: "member" as const };
    const sessionA = { ...browserSession, currentTenant: tenantA, contextVersion: "d".repeat(32) + ".1" };
    const contextB = "e".repeat(32) + ".2";
    const sessionB = { ...browserSession, currentTenant: tenantB, contextVersion: contextB, csrfToken: "f".repeat(64) };
    const sourceA = { ...readyDocument, documentId: "40000000-0000-4000-8000-000000000005", versionId: "40000000-0000-4000-8000-000000000006", generationId: "40000000-0000-4000-8000-000000000007", filename: "enterprise-a.txt" };
    const sourceB = { ...readyDocument, documentId: "40000000-0000-4000-8000-000000000008", versionId: "40000000-0000-4000-8000-000000000009", generationId: "40000000-0000-4000-8000-00000000000a", filename: "enterprise-b.txt" };
    let releaseOld!: (response: Response) => void;
    const oldResponse = new Promise<Response>(resolve => { releaseOld = resolve; });
    let documentRequests = 0;
    const documentSignals: AbortSignal[] = [];
    const calls: Array<{ path: string; context: string | null }> = [];
    const fetcher = vi.fn<Fetcher>((input, init = {}) => {
      const path = pathOf(input);
      const context = new Headers(init.headers).get("X-Session-Context");
      calls.push({ path, context });
      if (path === "/auth/session") return response(sessionA);
      if (path === "/auth/tenants") return response([tenantA, tenantB]);
      if (path === "/auth/tenant") return response(sessionB);
      if (path === "/health/ready") return response({ status: "ready", checks: { database: { status: "up" }, redis: { status: "up" }, object_store: { status: "up" } } });
      if (path === "/api/session") return response({ ...productSession(), tenantId: context === contextB ? tenantB.tenantId : tenantA.tenantId, actorId: context === contextB ? tenantB.actorId : tenantA.actorId });
      if (path === "/api/documents?limit=200") {
        documentRequests += 1;
        documentSignals.push(init.signal as AbortSignal);
        return documentRequests === 1 ? oldResponse : response([sourceB]);
      }
      if (path === "/api/presales") return response([]);
      throw new Error(`Unexpected HTTP boundary: ${path}`);
    });
    vi.stubGlobal("fetch", fetcher);
    window.history.replaceState(null, "", "/#/documents");
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0, refetchOnWindowFocus: false } } })}>
        <BrowserSessionBoundary entry={entry(false)}>{session => <App browserSession={session} />}</BrowserSessionBoundary>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(documentRequests).toBe(1));
    fireEvent.click(await screen.findByRole("button", { name: "Switch enterprise" }));
    fireEvent.click(await screen.findByRole("button", { name: "Open 企业 B" }));
    expect((await screen.findAllByText(sourceB.filename)).length).toBeGreaterThan(0);
    expect(screen.queryByText(sourceA.filename)).not.toBeInTheDocument();
    expect(documentSignals[0]?.aborted).toBe(true);
    releaseOld(json([sourceA]));
    await waitFor(() => expect(screen.queryByText(sourceA.filename)).not.toBeInTheDocument());
    const documentContexts = calls.filter(call => call.path === "/api/documents?limit=200").map(call => call.context);
    expect(documentContexts.length).toBeGreaterThanOrEqual(2);
    expect(documentContexts.every(context => context === sessionA.contextVersion || context === contextB)).toBe(true);
    expect(documentContexts).toContain(sessionA.contextVersion);
    expect(documentContexts).toContain(contextB);
  });
});
