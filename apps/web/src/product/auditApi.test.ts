import { afterEach, describe, expect, it, vi } from "vitest";

import { exportAuditEvents, fetchAuditEvents } from "./auditApi";

const page = {
  items: [
    {
      eventId: "01000000-0000-4000-8000-000000000001",
      tenantId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      actorId: "99999999-9999-4999-8999-999999999999",
      action: "agent_run.finished",
      resourceType: "agent_run",
      resourceId: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
      occurredAt: "2026-08-23T08:00:24Z",
      requestId: "req-1",
      correlationId: "corr-1",
      metadata: { status: "succeeded" },
      schemaVersion: 1,
    },
  ],
  nextCursor: null,
};

afterEach(() => vi.restoreAllMocks());

describe("fetchAuditEvents", () => {
  it("sends tenant authentication and serializes filters", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(page), { status: 200, headers: { "Content-Type": "application/json" } }),
    );

    await expect(fetchAuditEvents("local-token", { action: "agent_run.finished", resourceType: "agent_run" })).resolves.toEqual(page);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/audit-events?limit=100&action=agent_run.finished&resourceType=agent_run",
      expect.objectContaining({ headers: { Accept: "application/json", Authorization: "Bearer local-token" } }),
    );
  });

  it("rejects invalid success payloads and unsuccessful responses", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...page, items: [{ invalid: true }] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(null, { status: 503 }));

    await expect(fetchAuditEvents("token")).rejects.toThrow("schema is invalid");
    await expect(fetchAuditEvents("token")).rejects.toThrow("failed (503)");
  });
});

describe("exportAuditEvents", () => {
  it("requests a bounded CSV export with the active filters", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("event_id,action\n", { status: 200, headers: { "Content-Type": "text/csv" } }),
    );

    await expect(exportAuditEvents("local-token", {
      action: "agent_run.finished",
      resourceType: "agent_run",
      from: "2026-08-01T00:00:00Z",
      to: "2026-08-25T23:59:59Z",
    })).resolves.toBeInstanceOf(Blob);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/audit-events/export.csv?limit=2000&action=agent_run.finished&resourceType=agent_run&from=2026-08-01T00%3A00%3A00Z&to=2026-08-25T23%3A59%3A59Z",
      expect.objectContaining({ headers: { Accept: "text/csv", Authorization: "Bearer local-token" } }),
    );
  });

  it("rejects unsuccessful exports", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 503 }));
    await expect(exportAuditEvents("token")).rejects.toThrow("failed (503)");
  });
});
