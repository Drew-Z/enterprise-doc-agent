import { createHash } from "node:crypto";

import { describe, expect, it, vi } from "vitest";

import { hashFileSlices } from "./runner";

function sha256Hex(bytes: Uint8Array): string {
  return createHash("sha256").update(bytes).digest("hex");
}

function sha256Base64(bytes: Uint8Array): string {
  return createHash("sha256").update(bytes).digest("base64");
}

describe("hashFileSlices", () => {
  it.each([
    [new Uint8Array([1]), 4, 2],
    [new Uint8Array([1, 2, 3, 4]), 4, 3],
    [new Uint8Array([1, 2, 3, 4, 5]), 4, 2],
    [new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8, 9]), 4, 3],
  ])("hashes whole content and exact part boundaries", async (bytes, partSizeBytes, readChunkSizeBytes) => {
    const reads: Array<[number, number]> = [];
    const progress: number[] = [];

    const result = await hashFileSlices({
      sizeBytes: bytes.byteLength,
      partSizeBytes,
      readChunkSizeBytes,
      readSlice: (start, end) => {
        reads.push([start, end]);
        return Promise.resolve(bytes.slice(start, end).buffer);
      },
      onProgress: (processed) => progress.push(processed),
    });

    expect(result.wholeSha256).toBe(sha256Hex(bytes));
    expect(result.parts).toEqual(
      Array.from({ length: Math.ceil(bytes.byteLength / partSizeBytes) }, (_, index) => {
        const part = bytes.slice(index * partSizeBytes, Math.min((index + 1) * partSizeBytes, bytes.byteLength));
        return {
          partNumber: index + 1,
          sizeBytes: part.byteLength,
          checksumSha256: sha256Base64(part),
        };
      }),
    );
    expect(reads.every(([start, end]) => end - start <= Math.min(partSizeBytes, readChunkSizeBytes))).toBe(true);
    expect(progress).toEqual([...progress].sort((left, right) => left - right));
    expect(progress.at(-1)).toBe(bytes.byteLength);
  });

  it("caps reads at four MiB even when a larger chunk is requested", async () => {
    const sizeBytes = 4 * 1024 * 1024 + 1;
    const readSlice = vi.fn((start: number, end: number) => Promise.resolve(new Uint8Array(end - start).buffer));

    await hashFileSlices({
      sizeBytes,
      partSizeBytes: sizeBytes,
      readChunkSizeBytes: sizeBytes,
      readSlice,
    });

    expect(readSlice).toHaveBeenCalledTimes(2);
    expect(readSlice.mock.calls[0]?.[1] - (readSlice.mock.calls[0]?.[0] ?? 0)).toBe(4 * 1024 * 1024);
  });

  it("rejects cancellation and read failures with typed errors", async () => {
    const controller = new AbortController();
    controller.abort();
    await expect(
      hashFileSlices({
        sizeBytes: 1,
        partSizeBytes: 1,
        readSlice: () => Promise.resolve(new ArrayBuffer(1)),
        signal: controller.signal,
      }),
    ).rejects.toMatchObject({ code: "aborted" });

    await expect(
      hashFileSlices({
        sizeBytes: 1,
        partSizeBytes: 1,
        readSlice: () => Promise.reject(new Error("disk failure")),
      }),
    ).rejects.toEqual(expect.objectContaining({ code: "read_failed" }));
  });

  it("rejects short reads instead of hashing incomplete bytes", async () => {
    await expect(
      hashFileSlices({
        sizeBytes: 2,
        partSizeBytes: 2,
        readSlice: () => Promise.resolve(new ArrayBuffer(1)),
      }),
    ).rejects.toMatchObject({ code: "read_failed" });
  });
});
