import { afterEach, describe, expect, it, vi } from "vitest";

import {
  activateIdentityBinding,
  createIdentityBinding,
  deactivateIdentityBinding,
  fetchIdentityBindings,
  fetchIdentityMembers,
} from "./identityBindingsApi";

const tenantId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const bindingId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const userId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const binding = {
  bindingId,
  tenantId,
  issuer: "https://idp.example.com",
  subject: "subject-123",
  userId,
  userEmail: "member@example.com",
  isActive: true,
  createdAt: "2026-08-26T00:00:00+00:00",
  updatedAt: "2026-08-26T00:00:00+00:00",
};

function requestPath(input: RequestInfo | URL): string {
  return typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
}

afterEach(() => vi.restoreAllMocks());

describe("identity bindings API", () => {
  it("reads and mutates tenant bindings with bearer auth", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const method = init?.method ?? "GET";
      if (requestPath(input).includes("/members")) return Promise.resolve(new Response(JSON.stringify([{ userId, email: binding.userEmail, role: "member" }]), { status: 200 }));
      if (method === "POST") return Promise.resolve(new Response(JSON.stringify(binding), { status: 200 }));
      if (method === "DELETE") return Promise.resolve(new Response(JSON.stringify({ ...binding, isActive: false }), { status: 200 }));
      return Promise.resolve(new Response(JSON.stringify([binding]), { status: 200 }));
    });

    await expect(fetchIdentityBindings("local-token")).resolves.toEqual([binding]);
    await expect(fetchIdentityMembers("local-token", "member@example.com")).resolves.toEqual([{ userId, email: binding.userEmail, role: "member" }]);
    await expect(createIdentityBinding("local-token", { issuer: binding.issuer, subject: binding.subject, userId })).resolves.toEqual(binding);
    await expect(deactivateIdentityBinding("local-token", bindingId)).resolves.toMatchObject({ isActive: false });
    await expect(activateIdentityBinding("local-token", bindingId)).resolves.toMatchObject({ isActive: true });
    expect(fetchMock).toHaveBeenCalledTimes(5);
    expect(requestPath(fetchMock.mock.calls[0][0])).toBe("/api/identity-bindings");
    expect(requestPath(fetchMock.mock.calls[1][0])).toBe("/api/identity-bindings/members?q=member%40example.com");
    expect(fetchMock.mock.calls[2][1]).toEqual(expect.objectContaining({ method: "POST", body: JSON.stringify({ issuer: binding.issuer, subject: binding.subject, userId }) }));
    expect(requestPath(fetchMock.mock.calls[3][0])).toBe(`/api/identity-bindings/${bindingId}`);
    expect(requestPath(fetchMock.mock.calls[4][0])).toBe(`/api/identity-bindings/${bindingId}/activate`);
    expect(fetchMock.mock.calls[4][1]).toEqual(expect.objectContaining({ method: "POST" }));
    for (const [, init] of fetchMock.mock.calls) expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer local-token");
  });

  it("surfaces structured API errors and rejects invalid ids", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ error: { code: "external_identity_binding_conflict", message: "already bound", requestId: null } }), { status: 409 }));
    await expect(fetchIdentityBindings("local-token")).rejects.toMatchObject({ status: 409, code: "external_identity_binding_conflict" });
    expect(() => deactivateIdentityBinding("local-token", "invalid-id")).toThrow();
    expect(() => activateIdentityBinding("local-token", "invalid-id")).toThrow();
  });
});
