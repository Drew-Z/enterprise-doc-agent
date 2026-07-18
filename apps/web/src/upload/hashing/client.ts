import {
  HASH_PROTOCOL_VERSION,
  isHashWorkerResponse,
  type HashResult,
  type HashWorkerRequest,
} from "./protocol";

export interface HashWorkerLike {
  postMessage(message: HashWorkerRequest): void;
  terminate(): void;
  addEventListener(type: "message" | "error" | "messageerror", listener: EventListener): void;
  removeEventListener(type: "message" | "error" | "messageerror", listener: EventListener): void;
}

export interface StartHashJobOptions {
  partSizeBytes: number;
  readChunkSizeBytes?: number;
  onProgress?: (processedBytes: number, totalBytes: number) => void;
  workerFactory?: () => HashWorkerLike;
  jobIdFactory?: () => string;
}

export interface HashJob {
  jobId: string;
  result: Promise<HashResult>;
  cancel(): void;
}

export class HashWorkerClientError extends Error {
  constructor(readonly code: string, message: string) {
    super(message);
    this.name = "HashWorkerClientError";
  }
}

function defaultWorkerFactory(): HashWorkerLike {
  return new Worker(new URL("./hash.worker.ts", import.meta.url), { type: "module" });
}

export function startHashJob(file: File, options: StartHashJobOptions): HashJob {
  const jobId = options.jobIdFactory?.() ?? crypto.randomUUID();
  if (jobId.length === 0 || jobId.length > 128) {
    return {
      jobId,
      result: Promise.reject(new HashWorkerClientError("invalid_request", "Hash worker job ID is invalid.")),
      cancel() {
        // Invalid jobs never create a worker.
      },
    };
  }
  let worker: HashWorkerLike;
  try {
    worker = (options.workerFactory ?? defaultWorkerFactory)();
  } catch {
    return {
      jobId,
      result: Promise.reject(new HashWorkerClientError("worker_error", "Hash worker could not be created.")),
      cancel() {
        // There is no worker to cancel after construction fails.
      },
    };
  }
  let settled = false;
  let rejectResult: ((reason?: unknown) => void) | undefined;

  let messageListener: EventListener;
  let errorListener: EventListener;
  let messageErrorListener: EventListener;

  const cleanup = (): void => {
    worker.removeEventListener("message", messageListener);
    worker.removeEventListener("error", errorListener);
    worker.removeEventListener("messageerror", messageErrorListener);
    worker.terminate();
  };

  const rejectOnce = (error: HashWorkerClientError): void => {
    if (settled) {
      return;
    }
    settled = true;
    cleanup();
    rejectResult?.(error);
  };

  const result = new Promise<HashResult>((resolve, reject) => {
    rejectResult = reject;
    messageListener = (event): void => {
      if (!(event instanceof MessageEvent) || settled) {
        return;
      }
      const data: unknown = event.data;
      if (!isHashWorkerResponse(data)) {
        rejectOnce(new HashWorkerClientError("protocol_error", "Hash worker response is invalid."));
        return;
      }
      if (data.jobId !== jobId) {
        return;
      }
      if (data.type === "progress") {
        options.onProgress?.(data.processedBytes, data.totalBytes);
        return;
      }

      settled = true;
      cleanup();
      if (data.type === "completed") {
        resolve(data.result);
      } else {
        reject(new HashWorkerClientError(data.error.code, data.error.message));
      }
    };
    errorListener = () => rejectOnce(new HashWorkerClientError("worker_error", "Hash worker failed while running."));
    messageErrorListener = () =>
      rejectOnce(new HashWorkerClientError("protocol_error", "Hash worker returned an unreadable message."));

    try {
      worker.addEventListener("message", messageListener);
      worker.addEventListener("error", errorListener);
      worker.addEventListener("messageerror", messageErrorListener);
      worker.postMessage({
        version: HASH_PROTOCOL_VERSION,
        type: "start",
        jobId,
        file,
        partSizeBytes: options.partSizeBytes,
        ...(options.readChunkSizeBytes === undefined ? {} : { readChunkSizeBytes: options.readChunkSizeBytes }),
      });
    } catch {
      rejectOnce(new HashWorkerClientError("worker_error", "Hash worker could not start."));
    }
  });

  return {
    jobId,
    result,
    cancel() {
      if (settled) {
        return;
      }
      try {
        worker.postMessage({ version: HASH_PROTOCOL_VERSION, type: "cancel", jobId });
      } catch {
        // Termination below is still sufficient to stop a worker that cannot receive cancel.
      }
      rejectOnce(new HashWorkerClientError("aborted", "File hashing was canceled."));
    },
  };
}
