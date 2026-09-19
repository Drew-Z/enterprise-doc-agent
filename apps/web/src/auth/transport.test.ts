import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createApplicationCredentialStore } from "./credentialStore";
import { activateBrowserCredential, authenticatedFetch, captureBrowserCredential, configureAuthentication, retireBrowserCredential, scopedRecoveryKey, type Fetcher } from "./transport";

const context = { contextVersion: "a".repeat(32) + ".1", csrfToken: "c".repeat(64), tenantId: "tenant-a", actorId: "actor-a" };

beforeEach(() => configureAuthentication("browser"));
afterEach(() => { retireBrowserCredential(); configureAuthentication("bearer"); vi.unstubAllGlobals(); });

describe("application authentication transport", () => {
  it("sends cookie context for every read and adds CSRF only for writes", async () => {
    const credential = activateBrowserCredential(context);
    const fetcher = vi.fn<Fetcher>(() => Promise.resolve(new Response("{}")));
    await authenticatedFetch("/api/documents", credential, {}, fetcher);
    await authenticatedFetch("/api/upload-sessions", credential, { method: "POST" }, fetcher);
    const read = fetcher.mock.calls[0]?.[1] as RequestInit;
    const write = fetcher.mock.calls[1]?.[1] as RequestInit;
    expect(read).toMatchObject({ credentials: "same-origin", cache: "no-store", redirect: "error" });
    expect(new Headers(read.headers).get("X-Session-Context")).toBe(context.contextVersion);
    expect(new Headers(read.headers).has("Authorization")).toBe(false);
    expect(new Headers(read.headers).has("X-CSRF-Token")).toBe(false);
    expect(new Headers(write.headers).get("X-CSRF-Token")).toBe(context.csrfToken);
  });

  it("refuses foreign hosts, object transfers, mixed headers and stored bearer fallback", async () => {
    const credential = activateBrowserCredential(context);
    const fetcher = vi.fn();
    for (const url of ["https://other.example/api/documents", "/objects/presigned", "/auth/login"]) {
      await expect(authenticatedFetch(url, credential, {}, fetcher)).rejects.toThrow();
    }
    await expect(authenticatedFetch("/api/session", credential, { headers: { Authorization: "Bearer old" } }, fetcher)).rejects.toThrow();
    await expect(authenticatedFetch("/api/session", "saved-token", {}, fetcher)).rejects.toThrow();
    const readStorage = vi.fn(() => "saved-token");
    const storage = { getItem: readStorage, setItem: vi.fn(), removeItem: vi.fn() } as unknown as Storage;
    const store = createApplicationCredentialStore(storage);
    expect(store.load()).toBe(credential);
    retireBrowserCredential();
    expect(createApplicationCredentialStore(storage).load()).toBeNull();
    expect(readStorage).not.toHaveBeenCalled();
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("aborts old requests, discards late results and never binds old clients to a new tenant", async () => {
    const older = activateBrowserCredential(context);
    const store = createApplicationCredentialStore(sessionStorage);
    let finish!: (response: Response) => void;
    const fetcher = vi.fn<Fetcher>(() => new Promise<Response>(resolve => { finish = resolve; }));
    const pending = authenticatedFetch("/api/documents", older, {}, fetcher);
    const rejected = expect(pending).rejects.toMatchObject({ name: "AbortError" });
    activateBrowserCredential({ ...context, contextVersion: "b".repeat(32) + ".2", tenantId: "tenant-b" });
    expect((fetcher.mock.calls[0]?.[1] as RequestInit).signal?.aborted).toBe(true);
    expect(store.load()).toBe(older);
    finish(new Response('{"old":"evidence"}'));
    await rejected;
    await expect(authenticatedFetch("/api/documents", older, {}, fetcher)).rejects.toMatchObject({ name: "AbortError" });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(scopedRecoveryKey("run")).toBe("run:tenant-b:actor-a");
  });

  it("keeps explicit machine bearer requests free of browser cookies", async () => {
    configureAuthentication("bearer");
    const fetcher = vi.fn<Fetcher>(() => Promise.resolve(new Response("{}")));
    await authenticatedFetch("/api/session", "machine-token", {}, fetcher);
    expect(fetcher.mock.calls[0]?.[1]).toMatchObject({ credentials: "omit" });
    expect(new Headers((fetcher.mock.calls[0]?.[1] as RequestInit).headers).get("Authorization")).toBe("Bearer machine-token");
  });

  it("keeps ordinary document denials local but closes a revoked or stale enterprise session", async () => {
    const credential = activateBrowserCredential(context);
    const response = (status: number, code: string) => Promise.resolve(new Response(JSON.stringify({ error: { code } }), { status }));
    const forbidden = await authenticatedFetch("/api/documents", credential, {}, () => response(403, "document_access_denied"));
    expect(forbidden.status).toBe(403);
    expect(captureBrowserCredential()).toBe(credential);
    expect(credential.signal.aborted).toBe(false);
    for (const [status, code] of [[401, "authentication_required"], [403, "browser_principal_forbidden"], [409, "browser_context_stale"], [409, "browser_identity_conflict"]] as const) {
      const current = activateBrowserCredential(context);
      await expect(authenticatedFetch("/api/documents", current, {}, () => response(status, code))).rejects.toMatchObject({ name: "AbortError" });
      expect(current.signal.aborted).toBe(true);
      expect(captureBrowserCredential()).toBeNull();
    }
  });
});
