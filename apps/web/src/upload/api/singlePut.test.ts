import { describe, expect, it, vi } from "vitest";
import { UploadApiClient, UploadApiProtocolError } from "./client";
import { createUploadRequestSchema } from "./schemas";

const sessionId = "123e4567-e89b-42d3-a456-426614174000";
const checksumSha256 = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";
const request = { sizeBytes: 25, checksumSha256 };
const signedHeaders = {
  "Content-Length": "25",
  "If-None-Match": "*",
  "x-amz-meta-upload-session-id": sessionId,
  "x-amz-meta-declared-size": "25",
};
function setup(headers = signedHeaders, url = "https://objects.example/file") {
  const fetcher = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>().mockResolvedValue(new Response(JSON.stringify({
    url, headers, expiresInSeconds: 60,
  }), { status: 200 }));
  return { fetcher, client: new UploadApiClient({
    getToken: () => "test-token", fetcher,
    allowedObjectStoreOrigins: ["https://objects.example"],
  }) };
}

describe("single PUT control plane", () => {
  it("opts into transport without changing legacy requests", () => {
    const value = { filename: "file.txt", sizeBytes: 25, mediaType: "text/plain", sha256: "a".repeat(64) };
    expect(createUploadRequestSchema.parse(value)).toEqual(value);
    expect(createUploadRequestSchema.parse({ ...value, transport: "single_put" }).transport).toBe("single_put");
    expect(createUploadRequestSchema.safeParse({ ...value, transport: "unknown" }).success).toBe(false);
  });
  it("routes a bounded object and leaves Content-Length to the browser", async () => {
    const { client, fetcher } = setup();
    const result = await client.presignPart(sessionId, 1, request, undefined, "single_put");
    expect(fetcher.mock.calls[0]?.[0]).toBe(`/api/upload-sessions/${sessionId}/object/presign`);
    expect(fetcher.mock.calls[0]?.[1]?.body).toBe("{}");
    expect(result.headers).not.toHaveProperty("content-length");
    expect(result.headers["if-none-match"]).toBe("*");
    expect(result.checksumSha256).toBe(checksumSha256);
  });
  it.each([
    { "Content-Length": "26" },
    { "If-None-Match": "etag" },
    { "x-amz-meta-upload-session-id": "foreign" },
    { "x-amz-meta-declared-size": "26" },
    { "content-length": "25" },
    { "Authorization": "secret" },
  ])("rejects mismatched, duplicated or credential headers %j", async (override) => {
    const { client } = setup({ ...signedHeaders, ...override });
    await expect(client.presignPart(sessionId, 1, request, undefined, "single_put"))
      .rejects.toBeInstanceOf(UploadApiProtocolError);
  });
  it("rejects an unapproved object origin", async () => {
    const { client } = setup(signedHeaders, "https://other.example/file");
    await expect(client.presignPart(sessionId, 1, request, undefined, "single_put"))
      .rejects.toBeInstanceOf(UploadApiProtocolError);
  });
  it("rejects oversized objects before any request", async () => {
    const { client, fetcher } = setup();
    await expect(client.presignPart(sessionId, 1, { ...request, sizeBytes: 1_048_577 }, undefined, "single_put"))
      .rejects.toBeInstanceOf(UploadApiProtocolError);
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("completes through the object route with an empty body", async () => {
    const { client, fetcher } = setup();
    fetcher.mockResolvedValueOnce(new Response(JSON.stringify({
      sessionId, status: "completed", documentId: sessionId, versionId: sessionId,
      completedAt: "2026-10-05T00:00:00Z", replayed: false,
    }), { status: 200 }));
    await client.completeSession(sessionId, { parts: [{ ...request, partNumber: 1, etag: "opaque" }] }, undefined, "single_put");
    expect(fetcher.mock.calls[0]?.[0]).toBe(`/api/upload-sessions/${sessionId}/object/complete`);
    expect(fetcher.mock.calls[0]?.[1]?.body).toBe("{}");
  });
});
