import { describe, expect, it } from "vitest";

import { aggregateUploadProgress } from "./progress";
import type { UploadPartState } from "./types";

function part(status: UploadPartState["status"], sizeBytes: number, uploadedBytes = 0): UploadPartState {
  return {
    partNumber: 1,
    startByte: 0,
    endByte: sizeBytes,
    sizeBytes,
    checksumSha256: "checksum",
    status,
    attempt: 1,
    uploadedBytes,
    etag: status === "uploaded" ? '"etag"' : null,
    errorCode: null,
  };
}

describe("aggregateUploadProgress", () => {
  it("weights completed and in-flight parts by bytes", () => {
    const progress = aggregateUploadProgress([
      part("uploaded", 5),
      { ...part("uploading", 15, 9), partNumber: 2 },
      { ...part("pending", 20), partNumber: 3 },
    ]);
    expect(progress).toEqual({ uploadedBytes: 14, totalBytes: 40, percent: 35 });
  });

  it("clamps invalid loaded bytes and handles an empty plan", () => {
    expect(aggregateUploadProgress([{ ...part("uploading", 5, 99) }]).uploadedBytes).toBe(5);
    expect(aggregateUploadProgress([])).toEqual({ uploadedBytes: 0, totalBytes: 0, percent: 0 });
  });
});
