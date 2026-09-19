import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { BrowserSessionController } from "./sessionController";
import { captureBrowserCredential, configureAuthentication, type Fetcher } from "./transport";

const tenant = { tenantId: "00000000-0000-4000-8000-000000000001", actorId: "00000000-0000-4000-8000-000000000002", name: "企业甲", role: "owner" };
const session = { status: "authenticated", email: "owner@example.test", expiresAt: "2099-01-01T00:00:00Z", contextVersion: "a".repeat(32) + ".1", csrfToken: "c".repeat(64), currentTenant: tenant };
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });

beforeEach(() => configureAuthentication("browser"));
afterEach(() => { configureAuthentication("bearer"); vi.useRealTimers(); });

it("restores a selected enterprise and clears the old workspace before checking a remote change", async () => {
  const clearWorkspace = vi.fn();
  const fetcher = vi.fn<Fetcher>().mockResolvedValueOnce(json(session)).mockResolvedValueOnce(json({ status: "anonymous" }));
  const controller = new BrowserSessionController({ clearWorkspace, broadcast: vi.fn(), fetcher });
  await controller.reconcile();
  expect(controller.getSnapshot().phase).toBe("working");
  const credential = captureBrowserCredential();
  const pending = controller.reconcile("session_changed");
  expect(credential?.signal.aborted).toBe(true);
  expect(controller.getSnapshot().phase).toBe("loading");
  await pending;
  expect(controller.getSnapshot().phase).toBe("anonymous");
  expect(clearWorkspace).toHaveBeenCalledTimes(2);
  controller.stop();
});

it("uses the captured context for selection and installs only the returned enterprise", async () => {
  const selected = { ...session, contextVersion: "a".repeat(32) + ".2" };
  const fetcher = vi.fn<Fetcher>().mockResolvedValueOnce(json({ ...session, currentTenant: null })).mockResolvedValueOnce(json([tenant])).mockResolvedValueOnce(json(selected));
  const broadcast = vi.fn();
  const controller = new BrowserSessionController({ clearWorkspace: vi.fn(), broadcast, fetcher });
  await controller.reconcile();
  expect(controller.getSnapshot().phase).toBe("choosing");
  expect(captureBrowserCredential()).toBeNull();
  await controller.selectTenant(tenant.tenantId);
  const request = fetcher.mock.calls[2]?.[1];
  expect(new Headers(request?.headers).get("X-Session-Context")).toBe(session.contextVersion);
  expect(new Headers(request?.headers).get("X-CSRF-Token")).toBe(session.csrfToken);
  expect(captureBrowserCredential()?.contextVersion).toBe(selected.contextVersion);
  expect(broadcast).toHaveBeenCalledOnce();
  controller.stop();
});

it("preserves drafts and the captured credential when foreground verification confirms the same session", async () => {
  const clearWorkspace = vi.fn();
  let finish!: (value: Response) => void;
  const fetcher = vi.fn<Fetcher>().mockResolvedValueOnce(json(session)).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const controller = new BrowserSessionController({ clearWorkspace, broadcast: vi.fn(), fetcher });
  await controller.reconcile();
  const credential = captureBrowserCredential();
  const workspaceKey = controller.getSnapshot().workspaceKey;
  const pending = controller.reconcile();
  expect(credential?.signal.aborted).toBe(false);
  expect(controller.getSnapshot()).toMatchObject({ phase: "working", verifying: true });
  finish(json(session));
  await pending;
  expect(captureBrowserCredential()).toBe(credential);
  expect(controller.getSnapshot()).toMatchObject({ phase: "working", verifying: false, workspaceKey });
  expect(clearWorkspace).toHaveBeenCalledOnce();
  controller.stop();
});

it("keeps the workspace closed when logout is unconfirmed and does not restore on background refresh", async () => {
  const fetcher = vi.fn<Fetcher>().mockResolvedValueOnce(json(session)).mockRejectedValueOnce(new TypeError("offline")).mockResolvedValueOnce(json({ status: "anonymous" }));
  const controller = new BrowserSessionController({ clearWorkspace: vi.fn(), broadcast: vi.fn(), fetcher });
  await controller.reconcile();
  await controller.logout();
  expect(controller.getSnapshot().phase).toBe("logout-unconfirmed");
  expect(captureBrowserCredential()).toBeNull();
  await controller.reconcile();
  expect(fetcher).toHaveBeenCalledTimes(2);
  await controller.verifyAfterLogout();
  expect(controller.getSnapshot().phase).toBe("anonymous");
  controller.stop();
});

it("ignores a slow control response after the view has been invalidated", async () => {
  let finish!: (value: Response) => void;
  const fetcher = vi.fn<Fetcher>(() => new Promise(resolve => { finish = resolve; }));
  const controller = new BrowserSessionController({ clearWorkspace: vi.fn(), broadcast: vi.fn(), fetcher });
  const pending = controller.reconcile();
  controller.suspend();
  finish(json(session));
  await pending;
  expect(captureBrowserCredential()).toBeNull();
  expect(controller.getSnapshot().phase).toBe("loading");
  controller.stop();
});

it.each([{ status: "disabled" }, { status: "authenticated", token: "unexpected" }])("never falls back to saved tokens for disabled or malformed sign-in: %j", async value => {
  const controller = new BrowserSessionController({ clearWorkspace: vi.fn(), broadcast: vi.fn(), fetcher: vi.fn<Fetcher>().mockResolvedValue(json(value)) });
  await controller.reconcile();
  expect(["disabled", "unavailable"]).toContain(controller.getSnapshot().phase);
  expect(captureBrowserCredential()).toBeNull();
  controller.stop();
});

it("expires locally without leaving business content mounted", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-13T00:00:00Z"));
  const controller = new BrowserSessionController({ clearWorkspace: vi.fn(), broadcast: vi.fn(), fetcher: vi.fn<Fetcher>().mockResolvedValue(json({ ...session, expiresAt: "2026-09-13T00:00:01Z" })) });
  await controller.reconcile();
  const credential = captureBrowserCredential();
  await vi.advanceTimersByTimeAsync(1001);
  expect(credential?.signal.aborted).toBe(true);
  expect(controller.getSnapshot()).toMatchObject({ phase: "anonymous", notice: "expired" });
  controller.stop();
});
