import { StrictMode } from "react";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { UploadApiClient } from "./api/client";
import { useUploadController, type UploadWorkspaceDependencies } from "./controller";
import { CONTENT_INTENT_STORAGE_KEY, contentIntentSchema } from "./contentIntent";
import { UPLOAD_TOKEN_STORAGE_KEY } from "./persistence";

const id = "11111111-1111-4111-8111-111111111111";
const original = new File(["hello"], "note.txt", { type: "text/plain" });
const wrong = new File(["other"], "note.txt", { type: "text/plain" });
const session = { sessionId: id, filename: "note.txt", sizeBytes: 5, mediaType: "text/plain", declaredSha256: "a".repeat(64), extension: ".txt", partSizeBytes: 5, expectedPartCount: 1, expiresAt: "2026-10-07T00:00:00Z", status: "active", replayed: false, transport: "single_put" };
const completion = { sessionId: id, documentId: id, versionId: id, status: "completed", completedAt: "2026-10-06T00:00:00Z", replayed: false };
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

function setup(fetcher: typeof fetch, strict = false) {
  const uploadPart = vi.fn<UploadWorkspaceDependencies["uploadPart"]>(() => { throw new Error("unexpected object upload"); });
  const dependencies: UploadWorkspaceDependencies = {
    createApiClient: getToken => new UploadApiClient({ getToken, fetcher, allowedObjectStoreOrigins: ["https://objects.test"] }),
    startHashJob: file => ({ jobId: "hash", cancel: () => undefined, result: Promise.resolve({
      wholeSha256: (file === wrong ? "b" : "a").repeat(64),
      parts: [{ partNumber: 1, sizeBytes: 5, checksumSha256: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" }],
    }) }),
    uploadPart, idempotencyKeyFactory: () => "unused-new-key",
  };
  const hook = renderHook(() => useUploadController(dependencies, sessionStorage), strict ? { wrapper: StrictMode } : {});
  const select = () => act(() => { hook.result.current.dispatch({ type: "select_file", file: original, mediaType: "text/plain", idempotencyKey: "persisted-key" }); });
  return { ...hook, select, uploadPart };
}

beforeEach(() => { sessionStorage.clear(); sessionStorage.setItem(UPLOAD_TOKEN_STORAGE_KEY, "token"); });
afterEach(cleanup);

describe("single-request upload recovery through HTTP", () => {
  it("persists before submission and completes without a direct PUT", async () => {
    const fetcher = vi.fn<typeof fetch>((_path, init) => {
      const intent = contentIntentSchema.parse(JSON.parse(sessionStorage.getItem(CONTENT_INTENT_STORAGE_KEY)!) as unknown);
      expect(Object.keys(intent).sort()).toEqual(["idempotencyKey", "request", "version"]);
      expect(intent.idempotencyKey).toBe(new Headers(init?.headers).get("Idempotency-Key"));
      expect(JSON.stringify(intent)).not.toContain("contentBase64");
      return Promise.resolve(response({ session, completion }, 201));
    });
    const hook = setup(fetcher);
    hook.select();
    await waitFor(() => expect(hook.result.current.state.phase).toBe("completed"));
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(hook.uploadPart).not.toHaveBeenCalled();
    expect(sessionStorage.getItem(CONTENT_INTENT_STORAGE_KEY)).toBeNull();
  });

  it("recovers a lost completed response without resending content", async () => {
    const fetcher = vi.fn<typeof fetch>().mockRejectedValueOnce(new TypeError("lost response"))
      .mockResolvedValueOnce(response({ ...session, status: "completed", replayed: true }));
    const hook = setup(fetcher); hook.select();
    await waitFor(() => expect(hook.result.current.state.phase).toBe("completed"));
    expect(fetcher.mock.calls.map(call => call[0])).toEqual(["/api/upload-sessions/content", "/api/upload-sessions?includeSignature=true"]);
    expect(fetcher.mock.calls.every(call => new Headers(call[1]?.headers).get("Idempotency-Key") === "persisted-key")).toBe(true);
  });

  it("keeps an ambiguous completing request for explicit same-key retry", async () => {
    const fetcher = vi.fn<typeof fetch>().mockRejectedValueOnce(new TypeError("lost response"))
      .mockResolvedValueOnce(response({ ...session, status: "completing", replayed: true }))
      .mockResolvedValueOnce(response({ session: { ...session, replayed: true }, completion }));
    const hook = setup(fetcher); hook.select();
    await waitFor(() => expect(hook.result.current.state.reconciling).toBe(false));
    await waitFor(() => expect(hook.result.current.state.phase).toBe("failed"));
    await waitFor(() => expect(hook.result.current.state.contentStatus).toBe("completing"));
    act(() => { expect(hook.result.current.dispatch({ type: "cancel" })).toBe(false); expect(hook.result.current.dispatch({ type: "clear" })).toBe(false); });
    expect(sessionStorage.getItem(CONTENT_INTENT_STORAGE_KEY)).not.toBeNull();
    act(() => { expect(hook.result.current.dispatch({ type: "retry" })).toBe(true); });
    await waitFor(() => expect(hook.result.current.state.phase).toBe("completed"));
    expect(fetcher.mock.calls.every(call => new Headers(call[1]?.headers).get("Idempotency-Key") === "persisted-key")).toBe(true);
  });

  it("restores across reload, rejects wrong bytes, then reuses the original key", async () => {
    const pendingFetch = vi.fn<typeof fetch>(() => new Promise(() => undefined));
    const first = setup(pendingFetch); first.select();
    await waitFor(() => expect(pendingFetch).toHaveBeenCalledTimes(1));
    first.unmount();
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response({ ...session, status: "completing", replayed: true }))
      .mockResolvedValueOnce(response({ session, completion }));
    const restored = setup(fetcher);
    await waitFor(() => expect(restored.result.current.state.reconciling).toBe(false));
    expect(restored.result.current.state.phase).toBe("awaiting_file");
    act(() => { expect(restored.result.current.dispatch({ type: "reselect_file", file: wrong })).toBe(true); });
    await waitFor(() => expect(restored.result.current.state.failure?.code).toBe("different_file"));
    expect(fetcher).toHaveBeenCalledTimes(1);
    act(() => { expect(restored.result.current.dispatch({ type: "reselect_file", file: original })).toBe(true); });
    await waitFor(() => expect(restored.result.current.state.phase).toBe("completed"));
    expect(new Headers(fetcher.mock.calls[1][1]?.headers).get("Idempotency-Key")).toBe("persisted-key");
  });

  it("stops before network when the recovery record cannot be persisted", async () => {
    const fetcher = vi.fn<typeof fetch>();
    const hook = setup(fetcher);
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("full"); });
    hook.select();
    await waitFor(() => expect(hook.result.current.state.failure?.code).toBe("persistence_error"));
    expect(fetcher).not.toHaveBeenCalled();
    setItem.mockRestore();
  });

  it("restores an intent under StrictMode without leaving reconciliation stuck", async () => {
    sessionStorage.setItem(CONTENT_INTENT_STORAGE_KEY, JSON.stringify({ version: 1, idempotencyKey: "persisted-key", request: {
      filename: "note.txt", sizeBytes: 5, mediaType: "text/plain", sha256: "a".repeat(64), transport: "single_put",
    } }));
    const fetcher = vi.fn<typeof fetch>().mockImplementation(() => Promise.resolve(response({ ...session, status: "completed", replayed: true })));
    const hook = setup(fetcher, true);
    await waitFor(() => expect(hook.result.current.state.phase).toBe("completed"));
  });

  it("falls back only on an explicit unsupported route and retains the original key", async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(response({ error: { code: "upload_content_unsupported", message: "unsupported", requestId: null } }, 404))
      .mockResolvedValueOnce(response({ ...session, status: "completed", replayed: true }));
    const hook = setup(fetcher); hook.select();
    await waitFor(() => expect(hook.result.current.state.phase).toBe("completed"));
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(new Headers(fetcher.mock.calls[1][1]?.headers).get("Idempotency-Key")).toBe("persisted-key");
  });

  it("does not discard recovery until the server confirms cancellation", async () => {
    let finish!: (value: Response) => void;
    const fetcher = vi.fn<typeof fetch>().mockRejectedValueOnce(new TypeError("lost response"))
      .mockResolvedValueOnce(response({ ...session, replayed: true }))
      .mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    const hook = setup(fetcher); hook.select();
    await waitFor(() => expect(hook.result.current.state.contentStatus).toBe("active"));
    act(() => { expect(hook.result.current.dispatch({ type: "cancel" })).toBe(true); });
    expect(hook.result.current.state.phase).not.toBe("canceled");
    expect(sessionStorage.getItem(CONTENT_INTENT_STORAGE_KEY)).not.toBeNull();
    act(() => { finish(new Response(null, { status: 204 })); });
    await waitFor(() => expect(hook.result.current.state.phase).toBe("canceled"));
    expect(sessionStorage.getItem(CONTENT_INTENT_STORAGE_KEY)).toBeNull();
  });
});
