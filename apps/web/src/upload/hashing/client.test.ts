import { describe, expect, it, vi } from "vitest";

import { HASH_PROTOCOL_VERSION, type HashWorkerRequest } from "./protocol";
import { startHashJob, type HashWorkerLike } from "./client";

class FakeWorker implements HashWorkerLike {
  readonly posted: HashWorkerRequest[] = [];
  readonly terminate = vi.fn();
  throwOnPost = false;
  private readonly listeners = new Map<string, Set<EventListener>>();

  postMessage(message: HashWorkerRequest): void {
    if (this.throwOnPost) {
      throw new Error("post failed");
    }
    this.posted.push(message);
  }

  addEventListener(type: "message" | "error" | "messageerror", listener: EventListener): void {
    const listeners = this.listeners.get(type) ?? new Set<EventListener>();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type: "message" | "error" | "messageerror", listener: EventListener): void {
    this.listeners.get(type)?.delete(listener);
  }

  emit(data: unknown): void {
    for (const listener of this.listeners.get("message") ?? []) {
      listener(new MessageEvent("message", { data }));
    }
  }

  emitError(type: "error" | "messageerror"): void {
    for (const listener of this.listeners.get(type) ?? []) {
      listener(type === "error" ? new ErrorEvent("error") : new MessageEvent("messageerror"));
    }
  }
}

const checksum = btoa(String.fromCharCode(...new Uint8Array(32)));
const completedResult = {
  wholeSha256: "a".repeat(64),
  parts: [{ partNumber: 1, sizeBytes: 3, checksumSha256: checksum }],
};

describe("startHashJob", () => {
  it("filters other jobs, reports progress, resolves, and terminates", async () => {
    const worker = new FakeWorker();
    const onProgress = vi.fn();
    const job = startHashJob(new File(["abc"], "a.txt"), {
      partSizeBytes: 3,
      workerFactory: () => worker,
      jobIdFactory: () => "job-1",
      onProgress,
    });

    worker.emit({ version: HASH_PROTOCOL_VERSION, type: "progress", jobId: "other", processedBytes: 1, totalBytes: 3 });
    worker.emit({ version: HASH_PROTOCOL_VERSION, type: "progress", jobId: "job-1", processedBytes: 2, totalBytes: 3 });
    worker.emit({
      version: HASH_PROTOCOL_VERSION,
      type: "completed",
      jobId: "job-1",
      result: completedResult,
    });

    await expect(job.result).resolves.toEqual(completedResult);
    expect(onProgress).toHaveBeenCalledWith(2, 3);
    expect(worker.terminate).toHaveBeenCalledOnce();
  });

  it("cancels exactly once and ignores late worker messages", async () => {
    const worker = new FakeWorker();
    const job = startHashJob(new File(["abc"], "a.txt"), {
      partSizeBytes: 3,
      workerFactory: () => worker,
      jobIdFactory: () => "job-1",
    });

    job.cancel();
    job.cancel();
    worker.emit({
      version: HASH_PROTOCOL_VERSION,
      type: "completed",
      jobId: "job-1",
      result: completedResult,
    });

    await expect(job.result).rejects.toEqual(expect.objectContaining({ code: "aborted" }));
    expect(worker.posted.at(-1)).toMatchObject({ type: "cancel", jobId: "job-1" });
    expect(worker.terminate).toHaveBeenCalledOnce();
  });

  it.each([
    ["error" as const, "worker_error"],
    ["messageerror" as const, "protocol_error"],
  ])("rejects and terminates on worker %s", async (eventType, code) => {
    const worker = new FakeWorker();
    const job = startHashJob(new File(["abc"], "a.txt"), {
      partSizeBytes: 3,
      workerFactory: () => worker,
      jobIdFactory: () => "job-1",
    });
    worker.emitError(eventType);
    await expect(job.result).rejects.toMatchObject({ code });
    expect(worker.terminate).toHaveBeenCalledOnce();
  });

  it("maps worker construction and postMessage failures", async () => {
    const construction = startHashJob(new File(["abc"], "a.txt"), {
      partSizeBytes: 3,
      workerFactory: () => {
        throw new Error("load failed");
      },
      jobIdFactory: () => "job-1",
    });
    await expect(construction.result).rejects.toMatchObject({ code: "worker_error" });

    const worker = new FakeWorker();
    worker.throwOnPost = true;
    const posting = startHashJob(new File(["abc"], "a.txt"), {
      partSizeBytes: 3,
      workerFactory: () => worker,
      jobIdFactory: () => "job-2",
    });
    await expect(posting.result).rejects.toMatchObject({ code: "worker_error" });
    expect(worker.terminate).toHaveBeenCalledOnce();
  });

  it("rejects a malformed response for the active job", async () => {
    const worker = new FakeWorker();
    const job = startHashJob(new File(["abc"], "a.txt"), {
      partSizeBytes: 3,
      workerFactory: () => worker,
      jobIdFactory: () => "job-1",
    });
    worker.emit({
      version: HASH_PROTOCOL_VERSION,
      type: "completed",
      jobId: "job-1",
      result: { wholeSha256: "bad", parts: [] },
    });
    await expect(job.result).rejects.toMatchObject({ code: "protocol_error" });
    expect(worker.terminate).toHaveBeenCalledOnce();
  });

  it.each([null, "bad-message", { type: "completed" }])(
    "rejects malformed worker payload %j even without a job ID",
    async (payload) => {
      const worker = new FakeWorker();
      const job = startHashJob(new File(["abc"], "a.txt"), {
        partSizeBytes: 3,
        workerFactory: () => worker,
        jobIdFactory: () => "job-1",
      });
      worker.emit(payload);
      await expect(job.result).rejects.toMatchObject({ code: "protocol_error" });
      expect(worker.terminate).toHaveBeenCalledOnce();
    },
  );
});
