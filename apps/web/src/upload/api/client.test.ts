import { describe, expect, it, vi } from "vitest";

import { UploadApiClient, UploadApiProtocolError, UploadAuthenticationError } from "./client";

const sessionId = "11111111-1111-4111-8111-111111111111";
const responseBody = {
  sessionId,
  status: "active",
  filename: "contract.pdf",
  extension: ".pdf",
  mediaType: "application/pdf",
  sizeBytes: 5,
  declaredSha256: "a".repeat(64),
  partSizeBytes: 5,
  expectedPartCount: 1,
  expiresAt: "2026-07-18T00:00:00Z",
  replayed: false,
};
const checksum = btoa(String.fromCharCode(...new Uint8Array(32)));
const objectStoreOptions = { allowedObjectStoreOrigins: ["http://127.0.0.1:9000"] } as const;

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("UploadApiClient", () => {
  it("invokes an injected fetcher without binding the client as this", async () => {
    const observedContexts: unknown[] = [];
    const getResponseBody: Record<string, unknown> = { ...responseBody };
    delete getResponseBody.replayed;
    const fetcher = vi.fn(function (this: unknown) {
      observedContexts.push(this);
      return Promise.resolve(jsonResponse(200, { ...getResponseBody, uploadedParts: [] }));
    });
    const client = new UploadApiClient({ ...objectStoreOptions, getToken: () => "token", fetcher });

    await client.getSession(sessionId);

    expect(observedContexts).toEqual([undefined]);
  });

  it("sends authenticated create requests and accepts 201 or replay 200", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(jsonResponse(201, responseBody));
    const client = new UploadApiClient({ ...objectStoreOptions, baseUrl: "http://api.test/", getToken: () => "token", fetcher });

    await expect(
      client.createSession(
        { filename: "contract.pdf", sizeBytes: 5, mediaType: "application/pdf", sha256: "a".repeat(64) },
        "key-1",
      ),
    ).resolves.toMatchObject({ sessionId });

    const [url, init] = fetcher.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://api.test/api/upload-sessions");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer token");
    expect(new Headers(init.headers).get("Idempotency-Key")).toBe("key-1");
  });

  it("maps a strict typed error envelope", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      jsonResponse(409, { error: { code: "upload_idempotency_conflict", message: "Conflict.", requestId: null } }),
    );
    const client = new UploadApiClient({ ...objectStoreOptions, getToken: () => "token", fetcher });

    await expect(client.getSession(sessionId)).rejects.toEqual(
      expect.objectContaining({ status: 409, code: "upload_idempotency_conflict", requestId: null }),
    );
  });

  it("rejects invalid success and error response schemas", async () => {
    const successClient = new UploadApiClient({
      ...objectStoreOptions,
      getToken: () => "token",
      fetcher: vi.fn().mockResolvedValue(jsonResponse(200, { ...responseBody, session_id: sessionId })),
    });
    await expect(successClient.getSession(sessionId)).rejects.toBeInstanceOf(UploadApiProtocolError);

    const errorClient = new UploadApiClient({
      ...objectStoreOptions,
      getToken: () => "token",
      fetcher: vi.fn().mockResolvedValue(jsonResponse(500, { message: "raw failure" })),
    });
    await expect(errorClient.getSession(sessionId)).rejects.toBeInstanceOf(UploadApiProtocolError);
  });

  it("handles DELETE 204 without attempting to parse JSON", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    const client = new UploadApiClient({ ...objectStoreOptions, getToken: () => "token", fetcher });
    await expect(client.abortSession(sessionId)).resolves.toBeUndefined();
  });

  it("fails before fetch when the token or idempotency key is invalid", async () => {
    const fetcher = vi.fn();
    const client = new UploadApiClient({ ...objectStoreOptions, getToken: () => null, fetcher });
    await expect(client.getSession(sessionId)).rejects.toBeInstanceOf(UploadAuthenticationError);
    await expect(
      new UploadApiClient({ ...objectStoreOptions, getToken: () => "token", fetcher }).createSession(
        { filename: "a.pdf", sizeBytes: 1, mediaType: "application/pdf", sha256: "a".repeat(64) },
        "bad key",
      ),
    ).rejects.toBeInstanceOf(UploadApiProtocolError);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it.each([
    ["invalid session", () => new UploadApiClient({ ...objectStoreOptions, getToken: () => "token", fetcher: vi.fn() }).getSession("bad")],
    [
      "invalid part zero",
      () =>
        new UploadApiClient({ ...objectStoreOptions, getToken: () => "token", fetcher: vi.fn() }).presignPart(
          sessionId,
          0,
          { sizeBytes: 5, checksumSha256: checksum },
        ),
    ],
    [
      "invalid part NaN",
      () =>
        new UploadApiClient({ ...objectStoreOptions, getToken: () => "token", fetcher: vi.fn() }).presignPart(
          sessionId,
          Number.NaN,
          { sizeBytes: 5, checksumSha256: checksum },
        ),
    ],
  ])("rejects %s path parameters before network I/O", async (_name, request) => {
    await expect(request()).rejects.toBeInstanceOf(UploadApiProtocolError);
  });

  it("passes AbortSignal through fetch and maps cancellation", async () => {
    const controller = new AbortController();
    const fetcher = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      expect(init?.signal).toBe(controller.signal);
      controller.abort();
      return Promise.reject(new DOMException("Aborted", "AbortError"));
    });
    const client = new UploadApiClient({ ...objectStoreOptions, getToken: () => "token", fetcher });
    await expect(client.getSession(sessionId, controller.signal)).rejects.toEqual(
      expect.objectContaining({ code: "aborted" }),
    );
  });

  it("rejects a presigned URL outside the configured object-store origins", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        partNumber: 1,
        sizeBytes: 5,
        checksumSha256: checksum,
        url: "https://unexpected.example/bucket/key?signature=secret",
        headers: { "x-amz-checksum-sha256": checksum },
        expiresInSeconds: 300,
      }),
    );
    const client = new UploadApiClient({
      getToken: () => "token",
      fetcher,
      allowedObjectStoreOrigins: ["https://objects.example"],
    });
    await expect(
      client.presignPart(sessionId, 1, { sizeBytes: 5, checksumSha256: checksum }),
    ).rejects.toBeInstanceOf(UploadApiProtocolError);
  });

  it.each([
    { allowedObjectStoreOrigins: [] },
    { allowedObjectStoreOrigins: ["ftp://objects.example"] },
    { allowedObjectStoreOrigins: ["https://user:secret@objects.example"] },
    { allowedObjectStoreOrigins: ["https://objects.example/path"] },
    { allowedObjectStoreOrigins: ["https://objects.example?query=1"] },
  ])("rejects missing or non-origin object-store configuration", ({ allowedObjectStoreOrigins }) => {
    expect(
      () => new UploadApiClient({ getToken: () => "token", allowedObjectStoreOrigins }),
    ).toThrow(UploadApiProtocolError);
  });

  it("accepts a matching presign response with additional signed headers", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        partNumber: 1,
        sizeBytes: 5,
        checksumSha256: checksum,
        url: "http://127.0.0.1:9000/bucket/key?signature=secret",
        headers: { "x-amz-checksum-sha256": checksum, "x-extra-signed": "value" },
        expiresInSeconds: 300,
      }),
    );
    const client = new UploadApiClient({ ...objectStoreOptions, getToken: () => "token", fetcher });
    await expect(
      client.presignPart(sessionId, 1, { sizeBytes: 5, checksumSha256: checksum }),
    ).resolves.toMatchObject({ headers: { "x-extra-signed": "value" } });
  });

  it("accepts an empty header set for server-side readback verification", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        partNumber: 1,
        sizeBytes: 5,
        checksumSha256: checksum,
        url: "http://127.0.0.1:9000/bucket/key?signature=secret",
        headers: {},
        expiresInSeconds: 300,
      }),
    );
    const client = new UploadApiClient({ ...objectStoreOptions, getToken: () => "token", fetcher });
    await expect(
      client.presignPart(sessionId, 1, { sizeBytes: 5, checksumSha256: checksum }),
    ).resolves.toMatchObject({ headers: {} });
  });

  it.each([
    ["part number", { partNumber: 2 }],
    ["size", { sizeBytes: 4 }],
    ["checksum", { checksumSha256: btoa(String.fromCharCode(...new Uint8Array(32).fill(1))) }],
    ["non-checksum header without checksum mode", { headers: { "x-extra-signed": "value" } }],
    ["wrong checksum header", { headers: { "x-amz-checksum-sha256": "wrong" } }],
    [
      "duplicate checksum header",
      { headers: { "x-amz-checksum-sha256": checksum, "X-Amz-Checksum-Sha256": checksum } },
    ],
  ])("rejects presign response %s mismatch", async (_name, overrides) => {
    const fetcher = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        partNumber: 1,
        sizeBytes: 5,
        checksumSha256: checksum,
        url: "http://127.0.0.1:9000/bucket/key?signature=secret",
        headers: { "x-amz-checksum-sha256": checksum },
        expiresInSeconds: 300,
        ...overrides,
      }),
    );
    const client = new UploadApiClient({ ...objectStoreOptions, getToken: () => "token", fetcher });
    await expect(
      client.presignPart(sessionId, 1, { sizeBytes: 5, checksumSha256: checksum }),
    ).rejects.toBeInstanceOf(UploadApiProtocolError);
  });
});
