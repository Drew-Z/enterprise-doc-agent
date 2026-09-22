import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  UPLOAD_RECOVERY_STORAGE_KEY,
  UPLOAD_TOKEN_STORAGE_KEY,
  type HashJob,
  type HashResult,
  type PersistedUploadSession,
  type PresignPartRequest,
  type StartHashJobOptions,
  type UploadPartWithXhrOptions,
  type UploadWorkspaceDependencies,
} from ".";
import { UploadWorkspace } from "./UploadWorkspace";
import { UploadApiError } from "./api/client";
import type { CompleteUploadResponse, CreateUploadResponse } from "./api/schemas";
import { activateBrowserCredential, configureAuthentication, retireBrowserCredential } from "../auth/transport";

const SESSION_ID = "11111111-1111-4111-8111-111111111111";
const DOCUMENT_ID = "22222222-2222-4222-8222-222222222222";
const VERSION_ID = "33333333-3333-4333-8333-333333333333";
const WHOLE_SHA256 = "a".repeat(64);
const OTHER_SHA256 = "b".repeat(64);
const PART_CHECKSUM = `${"A".repeat(43)}=`;

function completedHash(partSizeBytes: number, wholeSha256 = WHOLE_SHA256): HashResult {
  const sizes = partSizeBytes === 4 ? [4, 4] : [8];
  return {
    wholeSha256,
    parts: sizes.map((sizeBytes, index) => ({
      partNumber: index + 1,
      sizeBytes,
      checksumSha256: PART_CHECKSUM,
    })),
  };
}

function resolvedHashJob(result: HashResult): HashJob {
  return {
    jobId: "test-job",
    result: Promise.resolve(result),
    cancel: vi.fn(),
  };
}

function createDependencies(overrides: Partial<UploadWorkspaceDependencies> = {}) {
  const api = {
    createSession: vi.fn().mockResolvedValue({
      sessionId: SESSION_ID,
      status: "active" as const,
      filename: "notes.txt",
      extension: ".txt",
      mediaType: "text/plain",
      sizeBytes: 8,
      declaredSha256: WHOLE_SHA256,
      partSizeBytes: 4,
      expectedPartCount: 2,
      expiresAt: "2026-07-19T08:00:00+00:00",
      replayed: false,
    }),
    getSession: vi.fn().mockResolvedValue({
      sessionId: SESSION_ID,
      status: "active" as const,
      filename: "notes.txt",
      extension: ".txt",
      mediaType: "text/plain",
      sizeBytes: 8,
      declaredSha256: WHOLE_SHA256,
      partSizeBytes: 4,
      expectedPartCount: 2,
      expiresAt: "2026-07-19T08:00:00+00:00",
      uploadedParts: [],
    }),
    presignPart: vi.fn().mockImplementation((_sessionId: string, partNumber: number, request: PresignPartRequest) =>
      Promise.resolve({
        partNumber,
        sizeBytes: request.sizeBytes,
        checksumSha256: request.checksumSha256,
        url: `http://127.0.0.1:9000/documents/part-${partNumber}`,
        headers: { "x-amz-checksum-sha256": request.checksumSha256 },
        expiresInSeconds: 900,
      }),
    ),
    completeSession: vi.fn().mockResolvedValue({
      sessionId: SESSION_ID,
      status: "completed" as const,
      documentId: DOCUMENT_ID,
      versionId: VERSION_ID,
      completedAt: "2026-07-18T08:00:00+00:00",
      replayed: false,
    }),
    abortSession: vi.fn().mockResolvedValue(undefined),
  };
  const dependencies: UploadWorkspaceDependencies = {
    createApiClient: vi.fn(() => api),
    startHashJob: vi.fn((_file: File, options: StartHashJobOptions) =>
      resolvedHashJob(completedHash(options.partSizeBytes)),
    ),
    uploadPart: vi.fn((options: UploadPartWithXhrOptions) => {
      options.onProgress?.(options.body.size, options.body.size);
      return { result: Promise.resolve({ etag: `"etag-${options.body.size}"` }), abort: vi.fn() };
    }),
    idempotencyKeyFactory: () => crypto.randomUUID(),
    ...overrides,
  };
  return { api, dependencies };
}

beforeEach(() => {
  sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  configureAuthentication("bearer");
  vi.restoreAllMocks();
});

describe("UploadWorkspace", () => {
  it("keeps file selection disabled until a local token is saved", () => {
    const { dependencies } = createDependencies();
    render(<UploadWorkspace dependencies={dependencies} storage={sessionStorage} />);

    expect(screen.getByLabelText("Choose document")).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Local API token"), {
      target: { value: "header.payload.signature" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save token" }));

    expect(screen.getByLabelText("Choose document")).toBeEnabled();
    expect(sessionStorage.getItem(UPLOAD_TOKEN_STORAGE_KEY)).toBe("header.payload.signature");
  });

  it("runs the complete two-pass upload and renders the durable document result", async () => {
    sessionStorage.setItem(UPLOAD_TOKEN_STORAGE_KEY, "header.payload.signature");
    const { api, dependencies } = createDependencies();
    render(
      <StrictMode>
        <UploadWorkspace dependencies={dependencies} storage={sessionStorage} />
      </StrictMode>,
    );

    const file = new File(["12345678"], "notes.txt", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText("Choose document"), { target: { files: [file] } });

    expect(await screen.findByText("Upload complete")).toBeInTheDocument();
    expect(screen.getByText(DOCUMENT_ID)).toBeInTheDocument();
    expect(api.createSession).toHaveBeenCalledTimes(1);
    expect(api.presignPart).toHaveBeenCalledTimes(2);
    expect(api.completeSession).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem(UPLOAD_RECOVERY_STORAGE_KEY)).toBeNull();
  });

  it("checks recovery read-only but rejects different content before any transfer", async () => {
    const recovery: PersistedUploadSession = {
      version: 1,
      sessionId: SESSION_ID,
      filename: "notes.txt",
      sizeBytes: 8,
      declaredSha256: WHOLE_SHA256,
      partSizeBytes: 4,
      expiresAt: "2026-07-19T08:00:00+00:00",
    };
    sessionStorage.setItem(UPLOAD_TOKEN_STORAGE_KEY, "header.payload.signature");
    sessionStorage.setItem(UPLOAD_RECOVERY_STORAGE_KEY, JSON.stringify(recovery));
    const { api, dependencies } = createDependencies({
      startHashJob: vi.fn(() => resolvedHashJob(completedHash(4, OTHER_SHA256))),
    });
    render(<UploadWorkspace dependencies={dependencies} storage={sessionStorage} />);

    await waitFor(() => expect(screen.getByLabelText("Choose original document")).toBeEnabled());
    const wrongFile = new File(["abcdefgh"], "notes.txt", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText("Choose original document"), {
      target: { files: [wrongFile] },
    });

    expect(await screen.findByRole("alert")).toHaveTextContent("does not match the upload session");
    expect(api.getSession).toHaveBeenCalledTimes(1);
    expect(api.presignPart).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Choose original document")).toBeEnabled();
  });

  it("recovers a completed upload after reload without requiring a file or hash", async () => {
    sessionStorage.setItem(UPLOAD_TOKEN_STORAGE_KEY, "header.payload.signature");
    sessionStorage.setItem(UPLOAD_RECOVERY_STORAGE_KEY, JSON.stringify({ version: 1, sessionId: SESSION_ID, filename: "notes.txt", sizeBytes: 8, declaredSha256: WHOLE_SHA256, partSizeBytes: 4, expiresAt: "2026-07-19T08:00:00+00:00" }));
    const { api, dependencies } = createDependencies();
    api.getSession.mockResolvedValue({ ...(await api.getSession()), status: "completed" });
    render(<UploadWorkspace dependencies={dependencies} storage={sessionStorage} />);
    expect(await screen.findByText("Upload complete")).toBeInTheDocument();
    expect(dependencies.startHashJob).not.toHaveBeenCalled();
    expect(api.createSession).not.toHaveBeenCalled();
    expect(sessionStorage.getItem(UPLOAD_RECOVERY_STORAGE_KEY)).toBeNull();
    expect(screen.getByLabelText("Choose document")).toBeEnabled();
  });

  it("reconciles a lost completion response instead of trapping the next upload", async () => {
    sessionStorage.setItem(UPLOAD_TOKEN_STORAGE_KEY, "header.payload.signature");
    const { api, dependencies } = createDependencies();
    api.getSession.mockResolvedValue({ ...(await api.getSession()), status: "completed" });
    api.completeSession.mockRejectedValueOnce(new UploadApiError(500, "upload_completion_state_invalid", "Inconsistent state.", "req-complete"));
    render(<UploadWorkspace dependencies={dependencies} storage={sessionStorage} />);
    fireEvent.change(screen.getByLabelText("Choose document"), { target: { files: [new File(["12345678"], "notes.txt")] } });
    expect(await screen.findByText("Upload complete")).toBeInTheDocument();
    expect(api.completeSession).toHaveBeenCalledTimes(1);
    expect(api.createSession).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("surfaces the server request id when session creation fails", async () => {
    sessionStorage.setItem(UPLOAD_TOKEN_STORAGE_KEY, "header.payload.signature");
    const { api, dependencies } = createDependencies();
    api.createSession.mockRejectedValueOnce(
      new UploadApiError(503, "upload_session_unavailable", "Upload service unavailable.", "req-upload-1"),
    );
    render(<UploadWorkspace dependencies={dependencies} storage={sessionStorage} />);

    const file = new File(["12345678"], "notes.txt", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText("Choose document"), { target: { files: [file] } });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Upload service unavailable. (upload_session_unavailable · Request ID: req-upload-1)",
    );
  });

  it("uploads multiple files sequentially with separate sessions and continues after a retry", async () => {
    sessionStorage.setItem(UPLOAD_TOKEN_STORAGE_KEY, "header.payload.signature");
    const { api, dependencies } = createDependencies();
    const baseline = await api.createSession() as CreateUploadResponse;
    api.createSession.mockClear();
    api.createSession.mockImplementation((request: { filename: string }) => Promise.resolve({ ...baseline, filename: request.filename, sessionId: request.filename === "notes.txt" ? SESSION_ID : DOCUMENT_ID }));
    const result = await api.completeSession() as CompleteUploadResponse;
    api.completeSession.mockClear();
    api.completeSession.mockImplementation((sessionId: string) => Promise.resolve({ ...result, sessionId }));
    let first = true;
    dependencies.startHashJob = vi.fn((_file: File, options: StartHashJobOptions) => {
      if (first) { first = false; return { jobId: "failed", result: Promise.reject(new Error("Hash unavailable")), cancel: vi.fn() }; }
      return resolvedHashJob(completedHash(options.partSizeBytes));
    });
    render(<UploadWorkspace dependencies={dependencies} storage={sessionStorage} />);
    expect(screen.getByLabelText("Choose document")).toHaveAttribute("multiple");
    fireEvent.change(screen.getByLabelText("Choose document"), { target: { files: [new File(["12345678"], "notes.txt"), new File(["abcdefgh"], "next.txt")] } });
    await screen.findByRole("alert");
    expect(api.createSession).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Retry upload" }));
    await waitFor(() => expect(api.completeSession).toHaveBeenCalledTimes(2));
    expect(api.createSession).toHaveBeenNthCalledWith(1, expect.objectContaining({ filename: "notes.txt" }), expect.any(String));
    expect(api.createSession).toHaveBeenNthCalledWith(2, expect.objectContaining({ filename: "next.txt" }), expect.any(String));
    expect(api.completeSession).toHaveBeenNthCalledWith(1, SESSION_ID, expect.any(Object));
    expect(api.completeSession).toHaveBeenNthCalledWith(2, DOCUMENT_ID, expect.any(Object));
    expect(sessionStorage.getItem(UPLOAD_RECOVERY_STORAGE_KEY)).toBeNull();
  });

  it("accepts a folder and shows unsupported and empty files without blocking valid files", async () => {
    sessionStorage.setItem(UPLOAD_TOKEN_STORAGE_KEY, "header.payload.signature");
    const { api, dependencies } = createDependencies();
    render(<UploadWorkspace dependencies={dependencies} storage={sessionStorage} />);
    const good = new File(["12345678"], "notes.txt");
    Object.defineProperty(good, "webkitRelativePath", { value: "sources/notes.txt" });
    const picker = screen.getByLabelText("Choose folder");
    expect(picker).toHaveAttribute("webkitdirectory");
    fireEvent.change(picker, { target: { files: [good, new File(["image"], "photo.png"), new File([], "empty.txt")] } });
    await waitFor(() => expect(api.completeSession).toHaveBeenCalledOnce());
    expect(screen.getByText("sources/notes.txt")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("photo.png");
    expect(screen.getByRole("alert")).toHaveTextContent("empty.txt");
  });

  it("cancels hashing when the browser session retires and ignores a late hash result", async () => {
    configureAuthentication("browser");
    activateBrowserCredential({ contextVersion: "a".repeat(32) + ".1", csrfToken: "c".repeat(64), tenantId: "tenant-a", actorId: "actor-a" });
    let finish!: (value: HashResult) => void;
    const cancel = vi.fn();
    const { api, dependencies } = createDependencies({ startHashJob: () => ({ jobId: "pending-hash", result: new Promise(resolve => { finish = resolve; }), cancel }) });
    render(<UploadWorkspace dependencies={dependencies} storage={sessionStorage} />);
    expect(screen.queryByLabelText("Local API token")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Choose document"), { target: { files: [new File(["12345678"], "notes.txt", { type: "text/plain" })] } });
    act(() => { retireBrowserCredential(); });
    expect(cancel).toHaveBeenCalled();
    await act(async () => { finish(completedHash(8)); await Promise.resolve(); });
    expect(api.createSession).not.toHaveBeenCalled();
  });

  it("aborts active object transfers on enterprise change and never completes from their late results", async () => {
    configureAuthentication("browser");
    activateBrowserCredential({ contextVersion: "a".repeat(32) + ".1", csrfToken: "c".repeat(64), tenantId: "tenant-a", actorId: "actor-a" });
    const abort = vi.fn();
    const transfers: { signal?: AbortSignal; finish: (value: { etag: string }) => void }[] = [];
    const { api, dependencies } = createDependencies({ uploadPart: options => ({ abort, result: new Promise(resolve => { transfers.push({ signal: options.signal, finish: resolve }); }) }) });
    render(<UploadWorkspace dependencies={dependencies} storage={sessionStorage} />);
    fireEvent.change(screen.getByLabelText("Choose document"), { target: { files: [new File(["12345678"], "notes.txt", { type: "text/plain" })] } });
    await waitFor(() => expect(transfers).toHaveLength(2));
    act(() => { activateBrowserCredential({ contextVersion: "b".repeat(32) + ".2", csrfToken: "d".repeat(64), tenantId: "tenant-b", actorId: "actor-a" }); });
    expect(abort).toHaveBeenCalledTimes(2);
    expect(transfers.every(item => item.signal?.aborted)).toBe(true);
    await act(async () => { for (const item of transfers) item.finish({ etag: "stale-result" }); await Promise.resolve(); });
    expect(api.completeSession).not.toHaveBeenCalled();
  });
});
