import type { ZodType } from "zod";
import { authenticatedFetch, type ApiCredential } from "../../auth/transport";

import {
  completeUploadRequestSchema,
  completeUploadResponseSchema,
  contentUploadResponseSchema,
  createUploadRequestSchema,
  createUploadResponseSchema,
  errorResponseSchema,
  getUploadResponseSchema,
  partNumberSchema,
  presignPartRequestSchema,
  presignPartResponseSchema,
  presignObjectResponseSchema,
  sessionIdSchema,
  type CompleteUploadRequest,
  type CompleteUploadResponse,
  type ContentUploadResponse,
  type CreateUploadRequest,
  type CreateUploadResponse,
  type GetUploadResponse,
  type PresignPartRequest,
  type PresignPartResponse,
  type UploadTransport,
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
  getToken: () => ApiCredential | null;
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

function boundedBase64(file: File, signal?: AbortSignal): Promise<string> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new UploadNetworkError("aborted", "Upload API request was canceled."));
      return;
    }
    const reader = new FileReader();
    const abort = () => reader.abort();
    const cleanup = () => signal?.removeEventListener("abort", abort);
    reader.onload = () => {
      cleanup();
      if (typeof reader.result !== "string" || !reader.result.includes(";base64,")) {
        reject(new UploadApiProtocolError("File encoding failed."));
      } else resolve(reader.result.slice(reader.result.indexOf(",") + 1));
    };
    reader.onerror = () => { cleanup(); reject(new UploadApiProtocolError("File could not be read.")); };
    reader.onabort = () => { cleanup(); reject(new UploadNetworkError("aborted", "Upload API request was canceled.")); };
    signal?.addEventListener("abort", abort, { once: true });
    reader.readAsDataURL(file);
  });
}

export class UploadApiClient {
  private readonly baseUrl: string;
  private readonly fetcher: Fetcher;
  private readonly allowedObjectStoreOrigins: ReadonlySet<string>;
  private initialUpload: {
    sessionId: string;
    credential: ApiCredential;
    expiresAt: number;
    signature: Pick<PresignPartResponse, "url" | "headers" | "expiresInSeconds">;
  } | undefined;

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
    this.initialUpload = undefined;
    const credential = this.options.getToken();
    const startedAt = performance.now();
    const { initialUpload, ...session } = await this.requestJson(
      `/api/upload-sessions${request.transport === "single_put" ? "?includeSignature=true" : ""}`,
      createUploadResponseSchema.extend({ initialUpload: presignObjectResponseSchema.optional() }),
      [200, 201],
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json", "Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify(parseOutgoing(createUploadRequestSchema, request)),
        signal,
      },
    );
    if (initialUpload && request.transport === "single_put" && session.transport === "single_put" &&
        session.status === "active" && credential !== null && credential === this.options.getToken() &&
        !signal?.aborted) {
      this.initialUpload = {
        sessionId: session.sessionId, credential, signature: initialUpload,
        expiresAt: startedAt + Math.max(0, initialUpload.expiresInSeconds - 5) * 1_000,
      };
    }
    // Signed capabilities must never enter the controller's persisted session state.
    return session;
  }

  async uploadContent(
    request: CreateUploadRequest, idempotencyKey: string, file: File, signal?: AbortSignal,
  ): Promise<ContentUploadResponse> {
    const parsed = parseOutgoing(createUploadRequestSchema, request);
    if (parsed.transport !== "single_put" || file.size !== parsed.sizeBytes ||
        file.name !== parsed.filename || file.size > 1_048_576 ||
        !/^[\x21-\x7e]{1,128}$/.test(idempotencyKey)) {
      throw new UploadApiProtocolError("Content upload requires one matching bounded file and key.");
    }
    const credential = this.options.getToken();
    const contentBase64 = await boundedBase64(file, signal);
    if (credential !== this.options.getToken() || signal?.aborted) {
      throw new UploadNetworkError("aborted", "Upload identity changed before submission.");
    }
    const result = await this.requestJson("/api/upload-sessions/content", contentUploadResponseSchema, [200, 201], {
      method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({ filename: parsed.filename, sizeBytes: parsed.sizeBytes,
        mediaType: parsed.mediaType, sha256: parsed.sha256, contentBase64 }), signal,
    });
    const { session, completion } = result;
    if (session.filename !== parsed.filename || session.sizeBytes !== parsed.sizeBytes ||
        session.declaredSha256 !== parsed.sha256 ||
        (completion !== null && (completion.sessionId !== session.sessionId || session.transport !== "single_put")) ||
        (completion === null && session.transport === "single_put")) {
      throw new UploadApiProtocolError("Content upload receipt does not match the selected file.");
    }
    return result;
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
    transport: UploadTransport = "multipart",
  ): Promise<PresignPartResponse> {
    const parsedSessionId = parseOutgoing(sessionIdSchema, sessionId);
    const parsedPartNumber = parseOutgoing(partNumberSchema, partNumber);
    const parsedRequest = parseOutgoing(presignPartRequestSchema, request);
    if (transport === "single_put") {
      if (parsedPartNumber !== 1 || parsedRequest.sizeBytes > 1_048_576) {
        throw new UploadApiProtocolError("Single PUT requires one bounded part.");
      }
      const initial = this.initialUpload;
      this.initialUpload = undefined;
      const credential = this.options.getToken();
      const reusable = initial?.sessionId === parsedSessionId && initial.credential === credential &&
        performance.now() < initial.expiresAt && !signal?.aborted &&
        (typeof credential === "string" || (credential !== null && !credential.signal.aborted));
      const response = reusable ? initial.signature : await this.requestJson(
        `/api/upload-sessions/${encodeURIComponent(parsedSessionId)}/object/presign`,
        presignObjectResponseSchema,
        [200],
        { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}", signal },
      );
      const url = new URL(response.url);
      if (!this.allowedObjectStoreOrigins.has(url.origin) || url.username || url.password) {
        throw new UploadApiProtocolError("Presigned upload URL uses an unapproved object store origin.");
      }
      const headers: Record<string, string> = {};
      for (const [name, value] of Object.entries(response.headers)) {
        const lower = name.toLowerCase();
        if (Object.hasOwn(headers, lower) || !/^(content-length|if-none-match|x-amz-meta-[a-z0-9-]+)$/.test(lower)) {
          throw new UploadApiProtocolError("Single PUT response has invalid signed headers.");
        }
        headers[lower] = value;
      }
      if (headers["content-length"] !== String(parsedRequest.sizeBytes) ||
          headers["if-none-match"] !== "*" ||
          headers["x-amz-meta-upload-session-id"] !== parsedSessionId ||
          headers["x-amz-meta-declared-size"] !== String(parsedRequest.sizeBytes)) {
        throw new UploadApiProtocolError("Single PUT response does not match the requested object.");
      }
      // The browser supplies Content-Length from the Blob; scripts cannot set it.
      delete headers["content-length"];
      return { ...response, headers, partNumber: 1, ...parsedRequest };
    }
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
    const usesReadbackVerification = checksumHeaders.length === 0 && Object.keys(response.headers).length === 0;
    if (
      !usesReadbackVerification &&
      (checksumHeaders.length !== 1 || checksumHeaders[0]?.[1] !== request.checksumSha256)
    ) {
      throw new UploadApiProtocolError("Presigned upload response has invalid checksum headers.");
    }
    return response;
  }

  async completeSession(
    sessionId: string,
    request: CompleteUploadRequest,
    signal?: AbortSignal,
    transport: UploadTransport = "multipart",
  ): Promise<CompleteUploadResponse> {
    const parsedSessionId = parseOutgoing(sessionIdSchema, sessionId);
    const parsedRequest = parseOutgoing(completeUploadRequestSchema, request);
    if (transport === "single_put" &&
        (parsedRequest.parts.length !== 1 || parsedRequest.parts[0]?.partNumber !== 1 ||
         parsedRequest.parts[0].sizeBytes > 1_048_576)) {
      throw new UploadApiProtocolError("Single PUT completion requires one bounded part.");
    }
    return this.requestJson(
      `/api/upload-sessions/${encodeURIComponent(parsedSessionId)}/${transport === "single_put" ? "object/complete" : "complete"}`,
      completeUploadResponseSchema,
      [200],
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(transport === "single_put" ? {} : parsedRequest),
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
    if (token === null || (typeof token === "string" && token.trim() === "")) {
      throw new UploadAuthenticationError();
    }
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    try {
      const fetcher = this.fetcher;
      return await authenticatedFetch(`${this.baseUrl}${path}`, token, { ...init, headers }, fetcher);
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
