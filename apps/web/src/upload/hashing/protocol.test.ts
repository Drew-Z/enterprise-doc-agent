import { describe, expect, it } from "vitest";

import { HASH_PROTOCOL_VERSION, isHashWorkerResponse, parseHashWorkerRequest } from "./protocol";

const checksum = btoa(String.fromCharCode(...new Uint8Array(32)));

describe("hash worker protocol", () => {
  it("accepts strict versioned start and cancel messages", () => {
    const file = new File(["abc"], "a.txt");
    expect(
      parseHashWorkerRequest({
        version: HASH_PROTOCOL_VERSION,
        type: "start",
        jobId: "job",
        file,
        partSizeBytes: 3,
      }),
    ).not.toBeNull();
    expect(parseHashWorkerRequest({ version: HASH_PROTOCOL_VERSION, type: "cancel", jobId: "job" })).not.toBeNull();
  });

  it.each([
    { version: 2, type: "cancel", jobId: "job" },
    { version: HASH_PROTOCOL_VERSION, type: "unknown", jobId: "job" },
    { version: HASH_PROTOCOL_VERSION, type: "cancel", jobId: "job", extra: true },
    { version: HASH_PROTOCOL_VERSION, type: "start", jobId: "job", file: {}, partSizeBytes: 1 },
  ])("rejects invalid or extended messages", (message) => {
    expect(parseHashWorkerRequest(message)).toBeNull();
  });

  it("accepts strict progress, completion, and failure responses", () => {
    expect(
      isHashWorkerResponse({
        version: HASH_PROTOCOL_VERSION,
        type: "progress",
        jobId: "job",
        processedBytes: 2,
        totalBytes: 3,
      }),
    ).toBe(true);
    expect(
      isHashWorkerResponse({
        version: HASH_PROTOCOL_VERSION,
        type: "completed",
        jobId: "job",
        result: {
          wholeSha256: "a".repeat(64),
          parts: [{ partNumber: 1, sizeBytes: 3, checksumSha256: checksum }],
        },
      }),
    ).toBe(true);
    expect(
      isHashWorkerResponse({
        version: HASH_PROTOCOL_VERSION,
        type: "failed",
        jobId: "job",
        error: { code: "read_failed", message: "Read failed." },
      }),
    ).toBe(true);
  });

  it.each([
    { version: HASH_PROTOCOL_VERSION, type: "progress", jobId: "job", processedBytes: -1, totalBytes: 3 },
    { version: HASH_PROTOCOL_VERSION, type: "progress", jobId: "job", processedBytes: Number.NaN, totalBytes: 3 },
    { version: HASH_PROTOCOL_VERSION, type: "progress", jobId: "job", processedBytes: 4, totalBytes: 3 },
    {
      version: HASH_PROTOCOL_VERSION,
      type: "completed",
      jobId: "job",
      result: { wholeSha256: "bad", parts: [{ partNumber: 1, sizeBytes: 3, checksumSha256: checksum }] },
    },
    {
      version: HASH_PROTOCOL_VERSION,
      type: "completed",
      jobId: "job",
      result: { wholeSha256: "a".repeat(64), parts: [{ partNumber: 2, sizeBytes: 3, checksumSha256: checksum }] },
    },
    {
      version: HASH_PROTOCOL_VERSION,
      type: "failed",
      jobId: "job",
      error: { code: "unknown", message: "Failure." },
    },
  ])("rejects malformed worker responses", (message) => {
    expect(isHashWorkerResponse(message)).toBe(false);
  });
});
