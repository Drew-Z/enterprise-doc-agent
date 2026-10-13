import { describe, expect, it, vi } from "vitest";
import { UploadApiClient, UploadApiProtocolError } from "./client";

const sessionId = "123e4567-e89b-42d3-a456-426614174000";
const checksumSha256 = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";
const input = { filename: "file.txt", sizeBytes: 25, mediaType: "text/plain", sha256: "0".repeat(64), transport: "single_put" as const };
const signature = {
  url: "https://objects.example/file", expiresInSeconds: 60,
  headers: { "Content-Length": "25", "If-None-Match": "*", "x-amz-meta-upload-session-id": sessionId, "x-amz-meta-declared-size": "25" },
};
const session = {
  sessionId, status: "active", filename: "file.txt", extension: ".txt", mediaType: "text/plain",
  sizeBytes: 25, declaredSha256: input.sha256, partSizeBytes: 16_777_216,
  expectedPartCount: 1, expiresAt: "2027-01-01T00:00:00Z", replayed: false, transport: "single_put",
};
function setup(initialUpload: unknown = signature) {
  let token = "original-token";
  const fetcher = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>()
    .mockImplementation(() => Promise.resolve(new Response(JSON.stringify(signature))))
    .mockResolvedValueOnce(new Response(JSON.stringify({ ...session, ...(initialUpload === null ? {} : { initialUpload }) }), { status: 201 }));
  const client = new UploadApiClient({ getToken: () => token, fetcher, allowedObjectStoreOrigins: ["https://objects.example"] });
  return { client, fetcher, changeToken: () => { token = "new-token"; } };
}
const part = { sizeBytes: 25, checksumSha256 };

describe("initial upload signature", () => {
  it("uses one create request before the first PUT, strips capability from returned state, then resigns retries", async () => {
    const { client, fetcher } = setup();
    expect(await client.createSession(input, "create-1")).toEqual(session);
    expect(fetcher.mock.calls[0]?.[0]).toBe("/api/upload-sessions?includeSignature=true");
    const signed = await client.presignPart(sessionId, 1, part, undefined, "single_put");
    expect(signed.url).toBe(signature.url);
    expect(signed.headers).not.toHaveProperty("content-length");
    expect(fetcher).toHaveBeenCalledTimes(1);
    await client.presignPart(sessionId, 1, part, undefined, "single_put");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("works with older servers that omit the optional signature", async () => {
    const { client, fetcher } = setup(null);
    await client.createSession(input, "create-1");
    await client.presignPart(sessionId, 1, part, undefined, "single_put");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("does not reuse a capability after the captured credential changes", async () => {
    const { client, fetcher, changeToken } = setup();
    await client.createSession(input, "create-1");
    changeToken();
    await client.presignPart(sessionId, 1, part, undefined, "single_put");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("resigns an expired capability using a monotonic deadline", async () => {
    const clock = vi.spyOn(performance, "now").mockReturnValue(1_000);
    try {
      const { client, fetcher } = setup();
      await client.createSession(input, "create-1");
      clock.mockReturnValue(62_000);
      await client.presignPart(sessionId, 1, part, undefined, "single_put");
      expect(fetcher).toHaveBeenCalledTimes(2);
    } finally { clock.mockRestore(); }
  });

  it.each([
    { ...signature, url: "https://foreign.example/file" },
    { ...signature, headers: { ...signature.headers, Authorization: "unsafe" } },
    { ...signature, headers: { ...signature.headers, "x-amz-meta-upload-session-id": "foreign" } },
  ])("applies the existing capability checks to inline signatures", async (initialUpload) => {
    const { client } = setup(initialUpload);
    await client.createSession(input, "create-1");
    await expect(client.presignPart(sessionId, 1, part, undefined, "single_put")).rejects.toBeInstanceOf(UploadApiProtocolError);
  });
});
