import { createSHA256 } from "hash-wasm";

import type { HashResult } from "./protocol";

const DEFAULT_MAX_READ_CHUNK_BYTES = 4 * 1024 * 1024;
const BASE64_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

export class HashRunError extends Error {
  constructor(
    readonly code: "aborted" | "invalid_request" | "read_failed" | "hash_failed",
    message: string,
    options?: ErrorOptions,
  ) {
    super(message, options);
    this.name = "HashRunError";
  }
}

export interface HashRunnerOptions {
  sizeBytes: number;
  partSizeBytes: number;
  readChunkSizeBytes?: number;
  readSlice: (start: number, end: number) => Promise<ArrayBuffer>;
  signal?: AbortSignal;
  onProgress?: (processedBytes: number, totalBytes: number) => void;
}

function bytesToBase64(bytes: Uint8Array): string {
  let output = "";
  for (let index = 0; index < bytes.length; index += 3) {
    const first = bytes[index] ?? 0;
    const second = bytes[index + 1] ?? 0;
    const third = bytes[index + 2] ?? 0;
    const combined = (first << 16) | (second << 8) | third;
    output += BASE64_ALPHABET[(combined >> 18) & 63];
    output += BASE64_ALPHABET[(combined >> 12) & 63];
    output += index + 1 < bytes.length ? BASE64_ALPHABET[(combined >> 6) & 63] : "=";
    output += index + 2 < bytes.length ? BASE64_ALPHABET[combined & 63] : "=";
  }
  return output;
}

function assertNotAborted(signal: AbortSignal | undefined): void {
  if (signal?.aborted === true) {
    throw new HashRunError("aborted", "File hashing was canceled.");
  }
}

function validateOptions(options: HashRunnerOptions): number {
  if (!Number.isSafeInteger(options.sizeBytes) || options.sizeBytes <= 0) {
    throw new HashRunError("invalid_request", "File size must be a positive safe integer.");
  }
  if (!Number.isSafeInteger(options.partSizeBytes) || options.partSizeBytes <= 0) {
    throw new HashRunError("invalid_request", "Part size must be a positive safe integer.");
  }
  const requestedChunkSize = options.readChunkSizeBytes ?? DEFAULT_MAX_READ_CHUNK_BYTES;
  if (!Number.isSafeInteger(requestedChunkSize) || requestedChunkSize <= 0) {
    throw new HashRunError("invalid_request", "Read chunk size must be a positive safe integer.");
  }
  return Math.min(requestedChunkSize, options.partSizeBytes, DEFAULT_MAX_READ_CHUNK_BYTES);
}

export async function hashFileSlices(options: HashRunnerOptions): Promise<HashResult> {
  const readChunkSizeBytes = validateOptions(options);
  assertNotAborted(options.signal);

  try {
    const wholeHasher = await createSHA256();
    wholeHasher.init();
    const parts: HashResult["parts"] = [];
    let processedBytes = 0;

    for (let partStart = 0, partNumber = 1; partStart < options.sizeBytes; partNumber += 1) {
      const partEnd = Math.min(partStart + options.partSizeBytes, options.sizeBytes);
      const partHasher = await createSHA256();
      partHasher.init();

      for (let chunkStart = partStart; chunkStart < partEnd; chunkStart += readChunkSizeBytes) {
        assertNotAborted(options.signal);
        const chunkEnd = Math.min(chunkStart + readChunkSizeBytes, partEnd);
        let buffer: ArrayBuffer;
        try {
          buffer = await options.readSlice(chunkStart, chunkEnd);
        } catch (error) {
          if (options.signal?.aborted === true) {
            throw new HashRunError("aborted", "File hashing was canceled.", { cause: error });
          }
          throw new HashRunError("read_failed", "A file slice could not be read.", { cause: error });
        }
        assertNotAborted(options.signal);
        if (buffer.byteLength !== chunkEnd - chunkStart) {
          throw new HashRunError("read_failed", "A file slice returned an unexpected byte count.");
        }

        const bytes = new Uint8Array(buffer);
        wholeHasher.update(bytes);
        partHasher.update(bytes);
        processedBytes += bytes.byteLength;
        options.onProgress?.(processedBytes, options.sizeBytes);
      }

      parts.push({
        partNumber,
        sizeBytes: partEnd - partStart,
        checksumSha256: bytesToBase64(partHasher.digest("binary")),
      });
      partStart = partEnd;
    }

    return {
      wholeSha256: wholeHasher.digest("hex"),
      parts,
    };
  } catch (error) {
    if (error instanceof HashRunError) {
      throw error;
    }
    throw new HashRunError("hash_failed", "SHA-256 hashing failed.", { cause: error });
  }
}
