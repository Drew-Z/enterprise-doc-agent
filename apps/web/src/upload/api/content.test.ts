import { afterEach, describe, expect, it, vi } from "vitest";
import { activateBrowserCredential, captureBrowserCredential, configureAuthentication } from "../../auth/transport";
import { UploadApiClient, UploadApiProtocolError } from "./client";

const id = "11111111-1111-4111-8111-111111111111";
const request = { filename: "note.txt", sizeBytes: 5, mediaType: "text/plain", sha256: "a".repeat(64), transport: "single_put" as const };
const result = {
  session: { sessionId: id, filename: "note.txt", sizeBytes: 5, mediaType: "text/plain", declaredSha256: request.sha256, extension: ".txt", partSizeBytes: 5, expectedPartCount: 1, expiresAt: "2026-10-07T00:00:00Z", status: "active", replayed: false, transport: "single_put" },
  completion: { sessionId: id, documentId: id, versionId: id, status: "completed", completedAt: "2026-10-06T00:00:00Z", replayed: false },
};
afterEach(() => configureAuthentication("bearer"));

describe("bounded content upload HTTP client", () => {
  it("sends bounded bytes and the same key to the API, with a validated durable receipt", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(result), { status: 201 }));
    const api = new UploadApiClient({ getToken: () => "token", allowedObjectStoreOrigins: ["https://objects.test"], fetcher });
    expect(await api.uploadContent(request, "same-intent", new File(["hello"], "note.txt"))).toEqual(result);
    expect(fetcher).toHaveBeenCalledTimes(1);
    const [path, init] = fetcher.mock.calls[0];
    expect(path).toBe("/api/upload-sessions/content");
    expect(new Headers(init?.headers).get("Idempotency-Key")).toBe("same-intent");
    expect(typeof init?.body).toBe("string");
    expect(JSON.parse(init?.body as string) as unknown).toEqual({ filename: "note.txt", sizeBytes: 5, mediaType: "text/plain", sha256: request.sha256, contentBase64: "aGVsbG8=" });
  });

  it("rejects oversized files before reading or sending them", async () => {
    const fetcher = vi.fn<typeof fetch>();
    const api = new UploadApiClient({ getToken: () => "token", allowedObjectStoreOrigins: ["https://objects.test"], fetcher });
    await expect(api.uploadContent({ ...request, sizeBytes: 1048577 }, "same", new File([new Uint8Array(1048577)], "note.txt"))).rejects.toBeInstanceOf(UploadApiProtocolError);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("rejects a receipt for another session", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ ...result, completion: { ...result.completion, sessionId: "22222222-2222-4222-8222-222222222222" } }), { status: 201 }));
    const api = new UploadApiClient({ getToken: () => "token", allowedObjectStoreOrigins: ["https://objects.test"], fetcher });
    await expect(api.uploadContent(request, "same", new File(["hello"], "note.txt"))).rejects.toBeInstanceOf(UploadApiProtocolError);
  });

  it("does not adopt a different enterprise while reading file bytes", async () => {
    configureAuthentication("browser");
    activateBrowserCredential({ contextVersion: "first", csrfToken: "csrf", tenantId: "first", actorId: "actor" });
    const fetcher = vi.fn<typeof fetch>();
    const api = new UploadApiClient({ getToken: captureBrowserCredential, allowedObjectStoreOrigins: ["https://objects.test"], fetcher });
    const pending = api.uploadContent(request, "same", new File(["hello"], "note.txt"));
    activateBrowserCredential({ contextVersion: "second", csrfToken: "csrf", tenantId: "second", actorId: "actor" });
    await expect(pending).rejects.toMatchObject({ code: "aborted" });
    expect(fetcher).not.toHaveBeenCalled();
  });
});
