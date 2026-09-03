import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createAuditLegalHold,
  fetchAuditLegalHolds,
  fetchAuditRetentionPolicy,
  fetchAuditRetentionPreview,
  archiveAuditRetentionPlan,
  fetchAuditArchiveDownload,
  releaseAuditLegalHold,
  updateAuditRetentionPolicy,
} from "./auditGovernanceApi";

const tenantId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const holdId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const resourceId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const policy = { tenantId, retentionDays: 365, isEnabled: true, updatedBy: tenantId };
const hold = {
  holdId,
  tenantId,
  name: "Investigation",
  reason: "Preserve related evidence.",
  resourceType: "document",
  resourceId,
  startsAt: "2026-08-26T00:00:00+00:00",
  expiresAt: null,
  releasedAt: null,
  createdBy: tenantId,
  releasedBy: null,
};

function requestPath(input: RequestInfo | URL): string {
  return typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
}

afterEach(() => vi.restoreAllMocks());

describe("audit governance API", () => {
  it("reads policy, preview, and legal holds with bearer auth", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const path = requestPath(input);
      if (path.endsWith("/retention-policy")) return Promise.resolve(new Response(JSON.stringify(policy), { status: 200 }));
      if (path.endsWith("/retention-preview")) return Promise.resolve(new Response(JSON.stringify({ cutoffAt: null, eligibleEventCount: 2, protectedEventCount: 1 }), { status: 200 }));
      return Promise.resolve(new Response(JSON.stringify([hold]), { status: 200 }));
    });

    await expect(fetchAuditRetentionPolicy("local-token")).resolves.toEqual(policy);
    await expect(fetchAuditRetentionPreview("local-token")).resolves.toMatchObject({ eligibleEventCount: 2 });
    await expect(fetchAuditLegalHolds("local-token")).resolves.toEqual([hold]);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    for (const [, init] of fetchMock.mock.calls) expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer local-token");
  });

  it("sends policy and hold mutations to the governance endpoints", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const path = requestPath(input);
      if (path.endsWith("/retention-policy")) return Promise.resolve(new Response(JSON.stringify(policy), { status: 200 }));
      return Promise.resolve(new Response(JSON.stringify(hold), { status: 201 }));
    });

    await updateAuditRetentionPolicy("local-token", { retentionDays: 180, isEnabled: false });
    await createAuditLegalHold("local-token", { name: "Investigation", reason: "Preserve related evidence.", resourceType: "document", resourceId });
    await releaseAuditLegalHold("local-token", holdId);

    expect(fetchMock.mock.calls[0][1]).toEqual(expect.objectContaining({ method: "PUT", body: JSON.stringify({ retentionDays: 180, isEnabled: false }) }));
    expect(fetchMock.mock.calls[1][1]).toEqual(expect.objectContaining({ method: "POST", body: JSON.stringify({ name: "Investigation", reason: "Preserve related evidence.", resourceType: "document", resourceId }) }));
    expect(fetchMock.mock.calls[2][0]).toBe(`/api/audit-governance/legal-holds/${holdId}`);
    expect(fetchMock.mock.calls[2][1]).toEqual(expect.objectContaining({ method: "DELETE" }));
  });

  it("surfaces structured API errors and rejects invalid ids", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ error: { code: "audit_governance_forbidden", message: "owner only", requestId: null } }), { status: 403 }));
    await expect(fetchAuditRetentionPolicy("local-token")).rejects.toMatchObject({ status: 403, code: "audit_governance_forbidden" });
    expect(() => releaseAuditLegalHold("local-token", "invalid-id")).toThrow();
  });

  it("writes a bounded archive snapshot through the owner endpoint", async () => {
    const archive = {
      batchId: holdId,
      tenantId,
      cutoffAt: "2026-08-26T00:00:00+00:00",
      archivedEventCount: 2,
      fingerprint: "a".repeat(64),
      bucket: "audit-archive",
      objectKey: "audit-archive/example.json",
      contentSha256: "b".repeat(64),
      sizeBytes: 512,
      createdBy: tenantId,
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(archive), { status: 201 }),
    );

    await expect(archiveAuditRetentionPlan("local-token", 25)).resolves.toEqual(archive);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/audit-governance/retention-archive?limit=25");
    expect(fetchMock.mock.calls[0][1]).toEqual(expect.objectContaining({ method: "POST" }));
  });

  it("prepares a short-lived archive download URL", async () => {
    const download = {
      batchId: holdId,
      tenantId,
      bucket: "audit-archive",
      objectKey: "audit-archive/example.json",
      contentSha256: "b".repeat(64),
      sizeBytes: 512,
      url: "https://archive.test/example.json?ttl=120",
      expiresInSeconds: 120,
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(download), { status: 200 }),
    );

    await expect(fetchAuditArchiveDownload("local-token", holdId, 120)).resolves.toEqual(download);
    expect(fetchMock.mock.calls[0][0]).toBe(
      `/api/audit-governance/retention-archives/${holdId}/download?expiresIn=120`,
    );
  });
});
