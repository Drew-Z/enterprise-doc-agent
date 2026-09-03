import { afterEach, describe, expect, it, vi } from "vitest";

import {
  activateTenantMember,
  changeTenantMemberRole,
  deactivateTenantMember,
  fetchTenantMembers,
  provisionTenantMember,
} from "./membersApi";

const member = {
  membershipId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  tenantId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  userId: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
  email: "member@example.com",
  role: "member" as const,
  isActive: true,
  createdAt: "2026-08-26T00:00:00+00:00",
  updatedAt: "2026-08-26T00:00:00+00:00",
};

function requestPath(input: RequestInfo | URL): string {
  return typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
}

afterEach(() => vi.restoreAllMocks());

describe("tenant members API", () => {
  it("supports the owner membership lifecycle", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      if ((init?.method ?? "GET") === "GET") return Promise.resolve(new Response(JSON.stringify([member]), { status: 200 }));
      return Promise.resolve(new Response(JSON.stringify(member), { status: 200 }));
    });

    await expect(fetchTenantMembers("token", "member@example.com")).resolves.toEqual([member]);
    await expect(provisionTenantMember("token", member.email, "member")).resolves.toEqual(member);
    await expect(changeTenantMemberRole("token", member.membershipId, "owner")).resolves.toEqual(member);
    await expect(deactivateTenantMember("token", member.membershipId)).resolves.toEqual(member);
    await expect(activateTenantMember("token", member.membershipId)).resolves.toEqual(member);

    expect(requestPath(fetchMock.mock.calls[0][0])).toBe("/api/members?q=member%40example.com");
    expect(fetchMock.mock.calls[1][1]).toEqual(expect.objectContaining({ method: "POST", body: JSON.stringify({ email: member.email, role: "member" }) }));
    expect(fetchMock.mock.calls[2][1]).toEqual(expect.objectContaining({ method: "PUT", body: JSON.stringify({ role: "owner" }) }));
    expect(fetchMock.mock.calls[3][1]).toEqual(expect.objectContaining({ method: "DELETE" }));
    expect(requestPath(fetchMock.mock.calls[4][0])).toBe(`/api/members/${member.membershipId}/activate`);
    for (const [, init] of fetchMock.mock.calls) expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer token");
  });

  it("surfaces stable safety errors", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ error: { code: "membership_last_owner_required", message: "one owner required", requestId: "req-members-1" } }), { status: 409 }));
    await expect(deactivateTenantMember("token", member.membershipId)).rejects.toMatchObject({ status: 409, code: "membership_last_owner_required", requestId: "req-members-1" });
  });
});
