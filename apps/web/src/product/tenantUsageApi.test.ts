import { afterEach, expect, it, vi } from "vitest";

import { activateBrowserCredential, configureAuthentication } from "../auth/transport";
import { tenantUsage, usageTenantA, usageTenantB, usageWithoutPeriod } from "../test/tenantUsage";
import { fetchTenantUsage } from "./tenantUsageApi";

function respond(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json", "X-Request-Id": "req-usage" } });
}

afterEach(() => { vi.restoreAllMocks(); configureAuthentication("bearer"); });

it("reads exact resources and nullable costs through the authenticated transport", async () => {
  const fetcher = vi.spyOn(globalThis, "fetch").mockResolvedValue(respond(tenantUsage()));
  const controller = new AbortController();
  await expect(fetchTenantUsage("token", usageTenantA, controller.signal)).resolves.toEqual(tenantUsage());
  const [url, init] = fetcher.mock.calls[0];
  expect(url).toBe("/api/tenant-usage");
  expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer token");
  expect(init).toMatchObject({ cache: "no-store", credentials: "omit", signal: controller.signal });
});

it.each(["legacy", "inactive"] as const)("preserves the %s state without inventing a period", async status => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(respond(usageWithoutPeriod(status)));
  await expect(fetchTenantUsage("token", usageTenantA)).resolves.toEqual(usageWithoutPeriod(status));
});

it("preserves precise decimal estimates, including database zero exponents", async () => {
  const value = tenantUsage({ costStatus: "known" });
  value.recentEvents[0].estimatedCost = "0.01234567";
  value.recentEvents[0].currency = "USD";
  const fetcher = vi.spyOn(globalThis, "fetch").mockResolvedValue(respond(value));
  expect((await fetchTenantUsage("token", usageTenantA)).recentEvents[0].estimatedCost).toBe("0.01234567");
  value.recentEvents[0].estimatedCost = "0E-8";
  fetcher.mockResolvedValue(respond(value));
  expect((await fetchTenantUsage("token", usageTenantA)).recentEvents[0].estimatedCost).toBe("0E-8");
});

it.each([
  { providerRequestsUsed: Number.MAX_SAFE_INTEGER + 1 },
  { providerRequestsReserved: -1 },
  { providerRequestLimit: "100" },
  { enabled: false },
  { periodEnd: "2026-08-01T00:00:00Z" },
  { resources: { ...tenantUsage().resources, storageRemainingBytes: 0 } },
  { unexpected: "data" },
])("rejects malformed or imprecise usage instead of displaying it: %j", async fields => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(respond({ ...tenantUsage(), ...fields }));
  await expect(fetchTenantUsage("token", usageTenantA)).rejects.toMatchObject({ code: "tenant_usage_invalid_response", requestId: "req-usage" });
});

it("rejects another tenant's response", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(respond(tenantUsage({ tenantId: usageTenantB })));
  await expect(fetchTenantUsage("token", usageTenantA)).rejects.toMatchObject({ code: "tenant_usage_context_mismatch" });
});

it("retains requestId and permission errors for user recovery", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(respond({ error: { code: "tenant_usage_forbidden", message: "Owner access required.", requestId: "req-owner" } }, 403));
  await expect(fetchTenantUsage("token", usageTenantA)).rejects.toMatchObject({ status: 403, code: "tenant_usage_forbidden", requestId: "req-owner" });
});

it("rejects a browser credential for another tenant before requesting data", async () => {
  configureAuthentication("browser");
  const credential = activateBrowserCredential({ tenantId: usageTenantA, actorId: "actor", contextVersion: "a".repeat(32) + ".1", csrfToken: "c".repeat(64) });
  const fetcher = vi.spyOn(globalThis, "fetch");
  await expect(fetchTenantUsage(credential, usageTenantB)).rejects.toMatchObject({ code: "tenant_usage_context_mismatch" });
  expect(fetcher).not.toHaveBeenCalled();
});

it("discards a response arriving after the browser changes enterprise", async () => {
  configureAuthentication("browser");
  const context = { actorId: "actor", contextVersion: "a".repeat(32) + ".1", csrfToken: "c".repeat(64) };
  const credential = activateBrowserCredential({ ...context, tenantId: usageTenantA });
  let resolve!: (value: Response) => void;
  vi.spyOn(globalThis, "fetch").mockImplementation(() => new Promise<Response>(done => { resolve = done; }));
  const pending = fetchTenantUsage(credential, usageTenantA);
  const rejection = expect(pending).rejects.toMatchObject({ name: "AbortError" });
  activateBrowserCredential({ ...context, tenantId: usageTenantB });
  resolve(respond(tenantUsage()));
  await rejection;
});

it("also fences a tenant switch while the response body is still streaming", async () => {
  configureAuthentication("browser");
  const context = { actorId: "actor", contextVersion: "a".repeat(32) + ".1", csrfToken: "c".repeat(64) };
  const credential = activateBrowserCredential({ ...context, tenantId: usageTenantA });
  let body!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({ start(controller) { body = controller; } });
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(stream));
  const pending = fetchTenantUsage(credential, usageTenantA);
  const rejection = expect(pending).rejects.toMatchObject({ name: "AbortError" });
  // Let fetch finish; the client is now waiting for the streaming JSON body.
  await new Promise<void>(resolve => setTimeout(resolve, 0));
  activateBrowserCredential({ ...context, tenantId: usageTenantB });
  body.enqueue(new TextEncoder().encode(JSON.stringify(tenantUsage())));
  body.close();
  await rejection;
});
