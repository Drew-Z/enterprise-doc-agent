import { describe, expect, it, vi } from "vitest";

import { HASH_PROTOCOL_VERSION, type HashWorkerResponse } from "./protocol";
import { HashRunError, type HashRunnerOptions } from "./runner";
import { createHashWorkerRuntime } from "./runtime";

const checksum = btoa(String.fromCharCode(...new Uint8Array(32)));
const result = {
  wholeSha256: "a".repeat(64),
  parts: [{ partNumber: 1, sizeBytes: 3, checksumSha256: checksum }],
};

async function flush(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

describe("hash worker runtime", () => {
  it("maps progress and completion for a valid job", async () => {
    const messages: HashWorkerResponse[] = [];
    const runHash = vi.fn((options: HashRunnerOptions) => {
      options.onProgress?.(2, 3);
      return Promise.resolve(result);
    });
    const runtime = createHashWorkerRuntime({ postMessage: (message) => messages.push(message) }, runHash);
    runtime.handleMessage({
      version: HASH_PROTOCOL_VERSION,
      type: "start",
      jobId: "job",
      file: new File(["abc"], "a.txt"),
      partSizeBytes: 3,
    });
    await flush();
    expect(messages).toEqual([
      expect.objectContaining({ type: "progress", jobId: "job", processedBytes: 2 }),
      expect.objectContaining({ type: "completed", jobId: "job", result }),
    ]);
  });

  it("aborts the matching job and reports typed runner failures", async () => {
    let signal: AbortSignal | undefined;
    const runHash = vi.fn((options: HashRunnerOptions) => {
      signal = options.signal;
      return Promise.reject(new HashRunError("read_failed", "Read failed."));
    });
    const messages: HashWorkerResponse[] = [];
    const runtime = createHashWorkerRuntime({ postMessage: (message) => messages.push(message) }, runHash);
    const file = new File(["abc"], "a.txt");
    runtime.handleMessage({ version: HASH_PROTOCOL_VERSION, type: "start", jobId: "job", file, partSizeBytes: 3 });
    runtime.handleMessage({ version: HASH_PROTOCOL_VERSION, type: "cancel", jobId: "job" });
    expect(signal?.aborted).toBe(true);
    await flush();
    expect(messages.at(-1)).toMatchObject({ type: "failed", error: { code: "read_failed" } });
  });

  it("rejects invalid and duplicate requests", () => {
    const messages: HashWorkerResponse[] = [];
    const runtime = createHashWorkerRuntime(
      { postMessage: (message) => messages.push(message) },
      () => new Promise(() => undefined),
    );
    const file = new File(["abc"], "a.txt");
    runtime.handleMessage({ version: 2, type: "start", jobId: "bad", file, partSizeBytes: 3 });
    runtime.handleMessage({ version: HASH_PROTOCOL_VERSION, type: "start", jobId: "job", file, partSizeBytes: 3 });
    runtime.handleMessage({ version: HASH_PROTOCOL_VERSION, type: "start", jobId: "job", file, partSizeBytes: 3 });
    expect(messages).toHaveLength(2);
    expect(messages[0]).toMatchObject({
      type: "failed",
      jobId: "bad",
      error: { code: "invalid_request" },
    });
    expect(messages[1]).toMatchObject({
      type: "failed",
      jobId: "job",
      error: { code: "invalid_request" },
    });
  });
});
