import type { ZodType } from "zod";

import {
  completeUploadRequestSchema,
  completeUploadResponseSchema,
  createUploadRequestSchema,
  createUploadResponseSchema,
  errorResponseSchema,
  getUploadResponseSchema,
  partNumberSchema,
  presignPartRequestSchema,
  presignPartResponseSchema,
  sessionIdSchema,
  type CompleteUploadRequest,
  type CompleteUploadResponse,
  type CreateUploadRequest,
  type CreateUploadResponse,
  type GetUploadResponse,
  type PresignPartRequest,
  type PresignPartResponse,
} from "./schemas";

type Fetcher = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

export class UploadApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly requestId: string | null,
  ) {
    super(message);
    this.name = "UploadApiError";
  }
}

export class UploadApiProtocolError extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "UploadApiProtocolError";
  }
}

export class UploadAuthenticationError extends Error {
  constructor() {
    super("An upload API token is required.");
    this.name = "UploadAuthenticationError";
  }
}

export class UploadNetworkError extends Error {
  constructor(readonly code: "aborted" | "network_error", message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "UploadNetworkError";
  }
}

export interface UploadApiClientOptions {
  baseUrl?: string;
  getToken: () => string | null;
  fetcher?: Fetcher;
  allowedObjectStoreOrigins: readonly string[];
}

function normalizeBaseUrl(value: string | undefined): string {
  return (value ?? "").replace(/\/+$/, "");
}

function parseOutgoing<T>(schema: ZodType<T>, value: unknown): T {
  const result = schema.safeParse(value);
  if (!result.success) {
    throw new UploadApiProtocolError("Upload API request does not match its runtime schema.", {
      cause: result.error,
    });
  }
  return result.data;
}

async function parseJson(response: Response): Promise<unknown> {
  try {
    return (await response.json()) as unknown;
  } catch (error) {
    throw new UploadApiProtocolError("Upload API returned invalid JSON.", { cause: error });
  }
}

export class UploadApiClient {
  private readonly baseUrl: string;
  private readonly fetcher: Fetcher;
  private readonly allowedObjectStoreOrigins: ReadonlySet<string>;

  constructor(private readonly options: UploadApiClientOptions) {
    this.baseUrl = normalizeBaseUrl(options.baseUrl);
    this.fetcher = options.fetcher ?? fetch;
    const configuredOrigins: unknown = options.allowedObjectStoreOrigins;
    if (!Array.isArray(configuredOrigins) || configuredOrigins.length === 0) {
      throw new UploadApiProtocolError("At least one object store origin is required.");
    }
    try {
      this.allowedObjectStoreOrigins = new Set(
        configuredOrigins.map((origin: unknown) => {
          if (typeof origin !== "string") {
            throw new TypeError("Object store allowlist entries must be strings.");
          }
          const parsed = new URL(origin);
          if (
            (parsed.protocol !== "http:" && parsed.protocol !== "https:") ||
            parsed.username !== "" ||
            parsed.password !== "" ||
            parsed.origin !== origin
          ) {
            throw new TypeError("Object store allowlist entries must be exact HTTP(S) origins.");
          }
          return parsed.origin;
        }),
      );
    } catch (error) {
      throw new UploadApiProtocolError("Object store origin allowlist is invalid.", { cause: error });
    }
  }

  async createSession(
    request: CreateUploadRequest,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<CreateUploadResponse> {
    if (!/^[\x21-\x7e]{1,128}$/.test(idempotencyKey)) {
      throw new UploadApiProtocolError("Idempotency key must be 1-128 visible ASCII characters.");
    }
    return this.requestJson(
      "/api/upload-sessions",
      createUploadResponseSchema,
      [200, 201],
      {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
        body: JSON.stringify(parseOutgoing(createUploadRequestSchema, request)),
        signal,
      },
    );
  }

  async getSession(sessionId: string, signal?: AbortSignal): Promise<GetUploadResponse> {
    const parsedSessionId = parseOutgoing(sessionIdSchema, sessionId);
    return this.requestJson(
      `/api/upload-sessions/${encodeURIComponent(parsedSessionId)}`,
      getUploadResponseSchema,
      [200],
      { signal },
    );
  }

  async presignPart(
    sessionId: string,
    partNumber: number,
    request: PresignPartRequest,
    signal?: AbortSignal,
  ): Promise<PresignPartResponse> {
    const parsedSessionId = parseOutgoing(sessionIdSchema, sessionId);
    const parsedPartNumber = parseOutgoing(partNumberSchema, partNumber);
    const response = await this.requestJson(
      `/api/upload-sessions/${encodeURIComponent(parsedSessionId)}/parts/${parsedPartNumber}/presign`,
      presignPartResponseSchema,
      [200],
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(parseOutgoing(presignPartRequestSchema, request)),
        signal,
      },
    );
    if (!this.allowedObjectStoreOrigins.has(new URL(response.url).origin)) {
      throw new UploadApiProtocolError("Presigned upload URL uses an unapproved object store origin.");
    }
    if (
      response.partNumber !== parsedPartNumber ||
      response.sizeBytes !== request.sizeBytes ||
      response.checksumSha256 !== request.checksumSha256
    ) {
      throw new UploadApiProtocolError("Presigned upload response does not match the requested part.");
    }
    const checksumHeaders = Object.entries(response.headers).filter(
      ([name]) => name.toLowerCase() === "x-amz-checksum-sha256",
    );
    if (checksumHeaders.length !== 1 || checksumHeaders[0]?.[1] !== request.checksumSha256) {
      throw new UploadApiProtocolError("Presigned upload response has invalid checksum headers.");
    }
    return response;
  }

  async completeSession(
    sessionId: string,
    request: CompleteUploadRequest,
    signal?: AbortSignal,
  ): Promise<CompleteUploadResponse> {
    const parsedSessionId = parseOutgoing(sessionIdSchema, sessionId);
    return this.requestJson(
      `/api/upload-sessions/${encodeURIComponent(parsedSessionId)}/complete`,
      completeUploadResponseSchema,
      [200],
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(parseOutgoing(completeUploadRequestSchema, request)),
        signal,
      },
    );
  }

  async abortSession(sessionId: string, signal?: AbortSignal): Promise<void> {
    const parsedSessionId = parseOutgoing(sessionIdSchema, sessionId);
    const response = await this.authorizedFetch(`/api/upload-sessions/${encodeURIComponent(parsedSessionId)}`, {
      method: "DELETE",
      signal,
    });
    if (response.status === 204) {
      return;
    }
    await this.throwResponseError(response);
  }

  private async requestJson<T>(
    path: string,
    schema: ZodType<T>,
    expectedStatuses: readonly number[],
    init?: RequestInit,
  ): Promise<T> {
    const response = await this.authorizedFetch(path, init);
    if (!expectedStatuses.includes(response.status)) {
      await this.throwResponseError(response);
    }
    const body = await parseJson(response);
    const result = schema.safeParse(body);
    if (!result.success) {
      throw new UploadApiProtocolError("Upload API response does not match its runtime schema.", {
        cause: result.error,
      });
    }
    return result.data;
  }

  private async authorizedFetch(path: string, init: RequestInit = {}): Promise<Response> {
    const token = this.options.getToken();
    if (token === null || token.trim() === "") {
      throw new UploadAuthenticationError();
    }
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    headers.set("Authorization", `Bearer ${token}`);
    try {
      const fetcher = this.fetcher;
      return await fetcher(`${this.baseUrl}${path}`, { ...init, headers });
    } catch (error) {
      if (init.signal?.aborted === true || (error instanceof DOMException && error.name === "AbortError")) {
        throw new UploadNetworkError("aborted", "Upload API request was canceled.", { cause: error });
      }
      throw new UploadNetworkError("network_error", "Upload API request failed.", { cause: error });
    }
  }

  private async throwResponseError(response: Response): Promise<never> {
    const body = await parseJson(response);
    const parsed = errorResponseSchema.safeParse(body);
    if (!parsed.success) {
      throw new UploadApiProtocolError("Upload API error response does not match its runtime schema.", {
        cause: parsed.error,
      });
    }
    throw new UploadApiError(
      response.status,
      parsed.data.error.code,
      parsed.data.error.message,
      parsed.data.error.requestId,
    );
  }
}
