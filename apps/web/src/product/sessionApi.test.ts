import { describe, expect, it, vi } from "vitest";

import { fetchProductSession, logoutProductSession } from "./sessionApi";

describe("fetchProductSession", () => {
  it("uses the authenticated server session contract", async () => {
    const payload = {
      tenantId: "11111111-1111-4111-8111-111111111111",
      actorId: "22222222-2222-4222-8222-222222222222",
      role: "owner",
      capabilities: {
        documentRead: true,
        documentWrite: true,
        agentRunCreate: true,
        auditRead: true,
        auditExport: true,
        approvalDecide: true,
      },
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } }),
    );

    await expect(fetchProductSession("local-token")).resolves.toEqual(payload);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/session",
      expect.objectContaining({ headers: { Accept: "application/json", Authorization: "Bearer local-token" } }),
    );
  });
});

describe("logoutProductSession", () => {
  it("revokes the authenticated local session through the API", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          revoked: true,
          alreadyRevoked: false,
          revokedAt: "2026-08-27T00:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    await expect(logoutProductSession("local-token")).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/session/logout",
      expect.objectContaining({
        method: "POST",
        headers: { Accept: "application/json", Authorization: "Bearer local-token" },
      }),
    );
  });
});
