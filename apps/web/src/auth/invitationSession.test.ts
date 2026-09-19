import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { consumeAuthEntry } from "./entry";
import { BrowserSessionController } from "./sessionController";
import { captureBrowserCredential, configureAuthentication, type Fetcher } from "./transport";

const tenant = { tenantId: "00000000-0000-4000-8000-000000000001", actorId: "00000000-0000-4000-8000-000000000002", name: "现有企业", role: "owner" };
const session = { status: "authenticated", email: "member@example.test", expiresAt: "2099-01-01T00:00:00Z", contextVersion: "a".repeat(32) + ".1", csrfToken: "c".repeat(64), currentTenant: tenant };
const token = "inv1_" + "x".repeat(43);
const preview = { tenantName: "受邀企业", expiresAt: "2099-01-01T00:00:00Z", state: "pending" };
const receipt = { tenantId: "00000000-0000-4000-8000-000000000003", tenantName: "受邀企业", membershipId: "00000000-0000-4000-8000-000000000004", replayed: false };
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });

beforeEach(() => configureAuthentication("browser"));
afterEach(() => { configureAuthentication("bearer"); window.history.replaceState(null, "", "/"); });

it("consumes an invitation fragment and releases its entry copy after handoff", () => {
  window.history.replaceState(null, "", "/#/invitation?token=" + token);
  const entry = consumeAuthEntry();
  expect(entry.invitationToken === token).toBe(true);
  expect(entry.invitationLink).toBe(true);
  expect(window.location.hash).toBe("");
  entry.releaseSecrets?.();
  expect(entry.invitationToken).toBeNull();
});

it("rejects duplicate or extra invitation parameters while clearing the fragment", () => {
  for (const suffix of ["&token=" + token, "&role=owner"]) {
    window.history.replaceState(null, "", "/#/invitation?token=" + token + suffix);
    expect(consumeAuthEntry().invitationToken).toBeNull();
    expect(window.location.hash).toBe("");
  }
});

it.each(["invitation", "admission"] as const)("rejects a second question mark in the %s fragment instead of ignoring its suffix", kind => {
  const credential = (kind === "invitation" ? "inv1_" : "adm1_") + "x".repeat(43);
  window.history.replaceState(null, "", `/#/${kind}?token=${credential}?extra=1`);
  const entry = consumeAuthEntry();
  expect((entry.invitationToken ?? entry.admissionToken) === null).toBe(true);
  expect(window.location.hash).toBe("");
});

it("previews from an existing selection and joins without automatically entering an enterprise", async () => {
  const fetcher = vi.fn<Fetcher>().mockResolvedValueOnce(json(session))
    .mockResolvedValueOnce(json(preview)).mockResolvedValueOnce(json(receipt))
    .mockResolvedValueOnce(json([tenant]));
  const controller = new BrowserSessionController({ fetcher, clearWorkspace: vi.fn(), broadcast: vi.fn() });
  await controller.reconcile();
  const old = captureBrowserCredential();
  expect(await controller.inspectInvitation(token)).toEqual(preview);
  expect(old?.signal.aborted).toBe(true);
  expect(await controller.acceptInvitation(token)).toBe(true);
  expect(controller.getSnapshot()).toMatchObject({ phase: "choosing", notice: "invitation_complete" });
  expect(captureBrowserCredential()).toBeNull();
  for (const [, init] of fetcher.mock.calls.slice(1, 3)) {
    expect(init?.body === JSON.stringify({ token })).toBe(true);
    expect(new Headers(init?.headers).get("X-CSRF-Token")).toBe(session.csrfToken);
    expect(init?.credentials).toBe("same-origin");
  }
  controller.stop();
});

it.each(["invitation", "admission"] as const)("does not apply an old %s success to a newer view while enterprise refresh is pending", async kind => {
  let reads = 0;
  let finish: ((value: Response) => void) | undefined;
  const fetcher = vi.fn<Fetcher>(url => {
    if (url === "/auth/session") return Promise.resolve(json({ ...session, currentTenant: null }));
    if (url === "/auth/tenants") {
      reads += 1;
      if (reads === 1) return Promise.resolve(json([]));
      return new Promise(resolve => { finish = resolve; });
    }
    return Promise.resolve(json(kind === "invitation" ? receipt : { tenantId: receipt.tenantId, replayed: false }));
  });
  const broadcast = vi.fn();
  const controller = new BrowserSessionController({ fetcher, clearWorkspace: vi.fn(), broadcast });
  await controller.reconcile();
  const pending = kind === "invitation" ? controller.acceptInvitation(token) : controller.acceptAdmission("adm1_" + "x".repeat(43), receipt.tenantName);
  await vi.waitFor(() => expect(finish).toBeTypeOf("function"));
  controller.suspend();
  finish?.(json([tenant]));
  expect(await pending).toBe(false);
  expect(controller.getSnapshot().phase).toBe("loading");
  expect(broadcast).toHaveBeenCalledTimes(1); // A confirmed server commit is not undone by retiring its UI.
  controller.stop();
});
