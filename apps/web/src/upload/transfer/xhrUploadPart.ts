export type XhrUploadFailureCode =
  | "aborted"
  | "http_error"
  | "missing_etag"
  | "network_error"
  | "progress_callback_error"
  | "setup_error"
  | "timeout";

export class XhrUploadError extends Error {
  constructor(
    readonly code: XhrUploadFailureCode,
    message: string,
    readonly status: number | null = null,
    options?: ErrorOptions,
  ) {
    super(message, options);
    this.name = "XhrUploadError";
  }
}

export interface XhrUploadTargetLike {
  onprogress: ((event: ProgressEvent<EventTarget>) => void) | null;
}

export interface XhrLike {
  readonly upload: XhrUploadTargetLike;
  status: number;
  timeout: number;
  onload: (() => void) | null;
  onerror: (() => void) | null;
  onabort: (() => void) | null;
  ontimeout: (() => void) | null;
  open(method: string, url: string, async: boolean): void;
  setRequestHeader(name: string, value: string): void;
  getResponseHeader(name: string): string | null;
  send(body: Blob): void;
  abort(): void;
}

export interface UploadPartWithXhrOptions {
  url: string;
  headers: Readonly<Record<string, string>>;
  body: Blob;
  timeoutMs?: number;
  signal?: AbortSignal;
  onProgress?: (uploadedBytes: number, totalBytes: number) => void;
  xhrFactory?: () => XhrLike;
}

export interface XhrUploadHandle {
  result: Promise<{ etag: string }>;
  abort(): void;
}

function defaultXhrFactory(): XhrLike {
  return new XMLHttpRequest() as unknown as XhrLike;
}

export function uploadPartWithXhr(options: UploadPartWithXhrOptions): XhrUploadHandle {
  let xhr: XhrLike;
  try {
    const parsedUrl = new URL(options.url);
    if (parsedUrl.protocol !== "http:" && parsedUrl.protocol !== "https:") {
      throw new TypeError("Part upload URL must use HTTP(S).");
    }
    if (
      options.timeoutMs !== undefined &&
      (!Number.isSafeInteger(options.timeoutMs) || options.timeoutMs < 0)
    ) {
      throw new TypeError("Part upload timeout must be a non-negative safe integer.");
    }
    xhr = (options.xhrFactory ?? defaultXhrFactory)();
  } catch (error) {
    return {
      result: Promise.reject(
        new XhrUploadError("setup_error", "Part upload could not be initialized.", null, { cause: error }),
      ),
      abort() {
        // There is no initialized request to abort.
      },
    };
  }
  let settled = false;
  let rejectResult: ((reason?: unknown) => void) | undefined;

  const cleanup = (): void => {
    options.signal?.removeEventListener("abort", abort);
    xhr.onload = null;
    xhr.onerror = null;
    xhr.onabort = null;
    xhr.ontimeout = null;
    xhr.upload.onprogress = null;
  };

  const fail = (error: XhrUploadError): void => {
    if (settled) {
      return;
    }
    settled = true;
    cleanup();
    rejectResult?.(error);
  };

  const abortRequest = (): void => {
    try {
      xhr.abort();
    } catch {
      // The typed failure that triggered abort remains authoritative.
    }
  };

  const abort = (): void => {
    if (settled) {
      return;
    }
    fail(new XhrUploadError("aborted", "Part upload was canceled."));
    abortRequest();
  };

  const result = new Promise<{ etag: string }>((resolve, reject) => {
    rejectResult = reject;
    xhr.upload.onprogress = (event) => {
      const loaded = Math.max(0, Math.min(event.loaded, options.body.size));
      try {
        options.onProgress?.(loaded, options.body.size);
      } catch (error) {
        fail(
          new XhrUploadError("progress_callback_error", "Part upload progress callback failed.", null, {
            cause: error,
          }),
        );
        abortRequest();
      }
    };
    xhr.onload = () => {
      if (xhr.status < 200 || xhr.status >= 300) {
        fail(new XhrUploadError("http_error", `Object store rejected the part with HTTP ${xhr.status}.`, xhr.status));
        return;
      }
      const etag = xhr.getResponseHeader("ETag");
      if (etag === null || etag.trim() === "") {
        fail(new XhrUploadError("missing_etag", "Object store response did not expose a usable ETag.", xhr.status));
        return;
      }
      if (settled) {
        return;
      }
      settled = true;
      cleanup();
      resolve({ etag });
    };
    xhr.onerror = () => fail(new XhrUploadError("network_error", "Part upload failed because of a network error."));
    xhr.onabort = () => fail(new XhrUploadError("aborted", "Part upload was canceled."));
    xhr.ontimeout = () => fail(new XhrUploadError("timeout", "Part upload timed out."));

    try {
      xhr.open("PUT", options.url, true);
      xhr.timeout = options.timeoutMs ?? 0;
      for (const [name, value] of Object.entries(options.headers)) {
        xhr.setRequestHeader(name, value);
      }
      if (options.signal?.aborted === true) {
        abort();
        return;
      }
      options.signal?.addEventListener("abort", abort, { once: true });
    } catch (error) {
      fail(new XhrUploadError("setup_error", "Part upload request setup failed.", null, { cause: error }));
      abortRequest();
      return;
    }
    try {
      xhr.send(options.body);
    } catch (error) {
      fail(new XhrUploadError("network_error", "Part upload could not be sent.", null, { cause: error }));
      abortRequest();
    }
  });

  return { result, abort };
}
