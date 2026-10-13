import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useUploadController, type UploadApiPort, type UploadWorkspaceDependencies } from "./controller";
import { XhrUploadError } from "./transfer/xhrUploadPart";
import { UPLOAD_TOKEN_STORAGE_KEY } from "./persistence";
import type { CreateUploadResponse, GetUploadResponse } from "./api/schemas";

const id = "11111111-1111-4111-8111-111111111111";
const file = new File(["hello"], "notes.txt", { type: "text/plain" });
const checksum = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";
const created: CreateUploadResponse = {
  sessionId: id, status: "active", filename: file.name, extension: ".txt", mediaType: file.type,
  sizeBytes: 5, declaredSha256: "a".repeat(64), partSizeBytes: 5, expectedPartCount: 1,
  expiresAt: "2026-10-06T00:00:00Z", replayed: false, transport: "single_put",
};
const observed: GetUploadResponse = {
  ...created, uploadedParts: [{ partNumber: 1, sizeBytes: 5, checksumSha256: checksum, etag: "verified" }],
};
function setup(read: () => Promise<GetUploadResponse> = () => Promise.resolve(observed)) {
  const api = {
    createSession: vi.fn<UploadApiPort["createSession"]>().mockResolvedValue(created),
    getSession: vi.fn<UploadApiPort["getSession"]>().mockImplementation(read),
    presignPart: vi.fn<UploadApiPort["presignPart"]>().mockResolvedValue({
      partNumber: 1, sizeBytes: 5, checksumSha256: checksum,
      url: "https://objects.example/file", headers: {}, expiresInSeconds: 60,
    }),
    completeSession: vi.fn<UploadApiPort["completeSession"]>().mockResolvedValue({
      sessionId: id, status: "completed", documentId: id, versionId: id,
      completedAt: "2026-10-05T00:00:00Z", replayed: false,
    }),
    abortSession: vi.fn<UploadApiPort["abortSession"]>().mockResolvedValue(undefined),
  };
  const dependencies: UploadWorkspaceDependencies = {
    createApiClient: () => api,
    startHashJob: () => ({ jobId: "hash", cancel: vi.fn(), result: Promise.resolve({
      wholeSha256: created.declaredSha256,
      parts: [{ partNumber: 1, sizeBytes: 5, checksumSha256: checksum }],
    }) }),
    uploadPart: () => ({ result: Promise.reject(new XhrUploadError("http_error", "Already exists", 412)), abort: vi.fn() }),
    idempotencyKeyFactory: () => "key",
  };
  const hook = renderHook(() => useUploadController(dependencies, sessionStorage));
  act(() => { hook.result.current.dispatch({ type: "select_file", file, mediaType: file.type, idempotencyKey: "key" }); });
  return { ...hook, api };
}
beforeEach(() => {
  sessionStorage.clear();
  sessionStorage.setItem(UPLOAD_TOKEN_STORAGE_KEY, "test-token");
});
afterEach(cleanup);

describe("single PUT conditional conflict recovery", () => {
  it("completes only after an authoritative matching read", async () => {
    const { api, result } = setup();
    await waitFor(() => expect(result.current.state.phase).toBe("completed"));
    expect(api.getSession).toHaveBeenCalledTimes(1);
    expect(api.completeSession).toHaveBeenCalledWith(id, { parts: observed.uploadedParts }, undefined, "single_put");
  });
  it.each([
    { ...observed, status: "aborted" as const },
    { ...observed, uploadedParts: [] },
    { ...observed, transport: "multipart" as const },
    { ...observed, uploadedParts: [{ ...observed.uploadedParts[0], checksumSha256: "wrong" }] },
    { ...observed, uploadedParts: [{ ...observed.uploadedParts[0], sizeBytes: 4 }] },
  ])("rejects nonmatching or terminal readback %#", async (value) => {
    const { api, result } = setup(() => Promise.resolve(value));
    await waitFor(() => expect(result.current.state.parts[0]?.status).toBe("failed"));
    expect(api.completeSession).not.toHaveBeenCalled();
  });
  it.each(["cancel", "pause"] as const)("ignores a late verified read after %s", async (command) => {
    let finish!: (value: GetUploadResponse) => void;
    const pending = new Promise<GetUploadResponse>((resolve) => { finish = resolve; });
    const { api, result } = setup(() => pending);
    await waitFor(() => expect(api.getSession).toHaveBeenCalledTimes(1));
    act(() => { expect(result.current.dispatch({ type: command })).toBe(true); });
    await act(async () => { finish(observed); await pending; });
    expect(result.current.state.phase).toBe(command === "pause" ? "paused" : "canceled");
    expect(api.completeSession).not.toHaveBeenCalled();
  });
});
