import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createDocumentGrant,
  deleteDocumentGrant,
  fetchDocumentAccess,
  fetchDocumentGrants,
  fetchDocumentInventory,
  updateDocumentAccess,
} from "./documentsApi";

const inventoryItem = {
  documentId: "33333333-3333-4333-8333-333333333333",
  title: "Security policy",
  accessMode: "restricted",
  canManage: true,
  versionId: "22222222-2222-4222-8222-222222222222",
  versionNumber: 2,
  filename: "security-policy.pdf",
  mediaType: "application/pdf",
  sizeBytes: 524_288,
  versionStatus: "failed",
  generationId: "44444444-4444-4444-8444-444444444444",
  ingestionStatus: "failed",
  ingestionStage: "embed",
  errorCode: "embedding_provider_unavailable",
  createdAt: "2026-08-22T04:30:00Z",
  updatedAt: "2026-08-23T04:30:00Z",
};

afterEach(() => vi.restoreAllMocks());

describe("fetchDocumentInventory", () => {
  it("sends bearer authentication and validates the response", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify([inventoryItem]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(fetchDocumentInventory("local-token")).resolves.toEqual([inventoryItem]);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/documents?limit=200",
      expect.objectContaining({
        headers: { Accept: "application/json", Authorization: "Bearer local-token" },
      }),
    );
  });

  it("rejects invalid success payloads and unsuccessful responses", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify([{ ...inventoryItem, versionNumber: 0 }]), { status: 200 }),
    ).mockResolvedValueOnce(new Response(null, { status: 503 }));

    await expect(fetchDocumentInventory("local-token")).rejects.toThrow("schema is invalid");
    await expect(fetchDocumentInventory("local-token")).rejects.toThrow("failed (503)");
  });

  it("preserves the server request id for an inventory failure", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ error: { code: "documents_unavailable", message: "Inventory unavailable.", requestId: "req-documents-1" } }), { status: 503 }),
    );

    await expect(fetchDocumentInventory("local-token")).rejects.toMatchObject({
      code: "documents_unavailable",
      requestId: "req-documents-1",
    });
  });

  it("manages document access and grants through authenticated endpoints", async () => {
    const documentId = inventoryItem.documentId;
    const grantId = "77777777-7777-4777-8777-777777777777";
    const userId = "88888888-8888-4888-8888-888888888888";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const path = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const method = init?.method ?? "GET";
      if (path.endsWith("/access") && method === "GET") {
        return Promise.resolve(new Response(JSON.stringify({ documentId, accessMode: "restricted", canManage: true }), { status: 200 }));
      }
      if (path.endsWith("/access") && method === "PUT") {
        return Promise.resolve(new Response(JSON.stringify({ documentId, accessMode: "tenant", canManage: true }), { status: 200 }));
      }
      if (path.endsWith("/grants") && method === "GET") {
        return Promise.resolve(new Response(JSON.stringify([{ grantId, documentId, granteeUserId: userId, granteeRole: null }]), { status: 200 }));
      }
      if (path.endsWith("/grants") && method === "POST") {
        return Promise.resolve(new Response(JSON.stringify({ grantId, documentId, granteeUserId: null, granteeRole: "member" }), { status: 201 }));
      }
      if (path.endsWith(`/grants/${grantId}`) && method === "DELETE") {
        return Promise.resolve(new Response(null, { status: 204 }));
      }
      return Promise.resolve(new Response(null, { status: 500 }));
    });

    await expect(fetchDocumentAccess("local-token", documentId)).resolves.toMatchObject({ accessMode: "restricted" });
    await expect(updateDocumentAccess("local-token", documentId, "tenant")).resolves.toMatchObject({ accessMode: "tenant" });
    await expect(fetchDocumentGrants("local-token", documentId)).resolves.toHaveLength(1);
    await expect(createDocumentGrant("local-token", documentId, { granteeRole: "member" })).resolves.toMatchObject({ granteeRole: "member" });
    await expect(deleteDocumentGrant("local-token", documentId, grantId)).resolves.toBeUndefined();

    const [, updateCall, , addCall] = fetchMock.mock.calls;
    expect(updateCall[1]).toEqual(expect.objectContaining({ method: "PUT", body: JSON.stringify({ accessMode: "tenant" }) }));
    expect(addCall[1]).toEqual(expect.objectContaining({ method: "POST", body: JSON.stringify({ granteeRole: "member" }) }));
    for (const [, init] of fetchMock.mock.calls) {
      expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer local-token");
    }
  });
});
