import {
  HASH_PROTOCOL_VERSION,
  parseHashWorkerRequest,
  type HashFailedMessage,
  type HashResult,
  type HashWorkerResponse,
} from "./protocol";
import { HashRunError, hashFileSlices, type HashRunnerOptions } from "./runner";

export interface HashWorkerPort {
  postMessage(message: HashWorkerResponse): void;
}

export type HashRunner = (options: HashRunnerOptions) => Promise<HashResult>;

export interface HashWorkerRuntime {
  handleMessage(value: unknown): void;
}

export function createHashWorkerRuntime(
  port: HashWorkerPort,
  runHash: HashRunner = hashFileSlices,
): HashWorkerRuntime {
  const jobs = new Map<string, AbortController>();

  const fail = (jobId: string, code: HashFailedMessage["error"]["code"], message: string): void => {
    port.postMessage({
      version: HASH_PROTOCOL_VERSION,
      type: "failed",
      jobId,
      error: { code, message },
    });
  };

  return {
    handleMessage(value) {
      const request = parseHashWorkerRequest(value);
      if (request === null) {
        const jobId =
          typeof value === "object" && value !== null && "jobId" in value && typeof value.jobId === "string"
            ? value.jobId
            : "unknown";
        fail(jobId, "invalid_request", "Hash worker request is invalid.");
        return;
      }

      if (request.type === "cancel") {
        jobs.get(request.jobId)?.abort();
        return;
      }
      if (jobs.has(request.jobId)) {
        fail(request.jobId, "invalid_request", "Hash worker job already exists.");
        return;
      }

      const controller = new AbortController();
      jobs.set(request.jobId, controller);
      void runHash({
        sizeBytes: request.file.size,
        partSizeBytes: request.partSizeBytes,
        readChunkSizeBytes: request.readChunkSizeBytes,
        readSlice: (start, end) => request.file.slice(start, end).arrayBuffer(),
        signal: controller.signal,
        onProgress: (processedBytes, totalBytes) => {
          port.postMessage({
            version: HASH_PROTOCOL_VERSION,
            type: "progress",
            jobId: request.jobId,
            processedBytes,
            totalBytes,
          });
        },
      })
        .then((result) => {
          port.postMessage({
            version: HASH_PROTOCOL_VERSION,
            type: "completed",
            jobId: request.jobId,
            result,
          });
        })
        .catch((error: unknown) => {
          const failure = error instanceof HashRunError
            ? error
            : new HashRunError("hash_failed", "SHA-256 hashing failed.", { cause: error });
          fail(request.jobId, failure.code, failure.message);
        })
        .finally(() => {
          jobs.delete(request.jobId);
        });
    },
  };
}
