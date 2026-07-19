import { z, type ZodType } from "zod";

import {
  agentArtifactDownloadResponseSchema,
  agentArtifactResponseSchema,
  agentRunEventResponseSchema,
  agentRunStatusResponseSchema,
  approvalDecisionRequestSchema,
  approvalDecisionResponseSchema,
  approvalRequestResponseSchema,
  createAgentRunRequestSchema,
  createAgentRunResponseSchema,
  errorResponseSchema,
  readyDocumentVersionSchema,
  type AgentArtifactDownloadResponse,
  type AgentArtifactResponse,
  type AgentRunEventResponse,
  type AgentRunStatusResponse,
  type ApprovalDecisionRequest,
  type ApprovalDecisionResponse,
  type ApprovalRequestResponse,
  type CreateAgentRunRequest,
  type CreateAgentRunResponse,
  type ReadyDocumentVersion,
} from "./schemas";

type Fetcher = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

export class AgentApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly requestId: string | null,
  ) {
    super(message);
    this.name = "AgentApiError";
  }
}

export class AgentApiProtocolError extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "AgentApiProtocolError";
  }
}

export class AgentAuthenticationError extends Error {
  constructor() {
    super("An Agent API token is required.");
    this.name = "AgentAuthenticationError";
  }
}

export class AgentNetworkError extends Error {
  constructor(readonly code: "aborted" | "network_error", message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "AgentNetworkError";
  }
}

export interface AgentApiClientOptions {
  baseUrl?: string;
  getToken: () => string | null;
  fetcher?: Fetcher;
}

export interface AgentApiClientProtocol {
  listReadyDocumentVersions(signal?: AbortSignal): Promise<ReadyDocumentVersion[]>;
  createRun(
    request: CreateAgentRunRequest,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<CreateAgentRunResponse>;
  getRun(runId: string, signal?: AbortSignal): Promise<AgentRunStatusResponse>;
  listEvents(runId: string, afterSequence?: number, signal?: AbortSignal): Promise<AgentRunEventResponse[]>;
  openEventStream(runId: string, lastSequence: number, signal?: AbortSignal): Promise<Response>;
  cancelRun(runId: string, signal?: AbortSignal): Promise<AgentRunStatusResponse>;
  getApproval(approvalId: string, signal?: AbortSignal): Promise<ApprovalRequestResponse>;
  decideApproval(
    approvalId: string,
    request: ApprovalDecisionRequest,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<ApprovalDecisionResponse>;
  listArtifacts(runId: string, signal?: AbortSignal): Promise<AgentArtifactResponse[]>;
  getArtifactDownload(artifactId: string, signal?: AbortSignal): Promise<AgentArtifactDownloadResponse>;
}

function normalizeBaseUrl(value: string | undefined): string {
  return (value ?? "").replace(/\/+$/, "");
}

function parseOutgoing<T>(schema: ZodType<T>, value: unknown): T {
  const result = schema.safeParse(value);
  if (!result.success) {
    throw new AgentApiProtocolError("Agent API request does not match its runtime schema.", {
      cause: result.error,
    });
  }
  return result.data;
}

async function parseJson(response: Response): Promise<unknown> {
  try {
    return (await response.json()) as unknown;
  } catch (error) {
    throw new AgentApiProtocolError("Agent API returned invalid JSON.", { cause: error });
  }
}

function validateIdempotencyKey(value: string): string {
  if (!/^[\x21-\x7e]{1,128}$/.test(value)) {
    throw new AgentApiProtocolError("Idempotency key must be 1-128 visible ASCII characters.");
  }
  return value;
}

export class AgentApiClient implements AgentApiClientProtocol {
  private readonly baseUrl: string;
  private readonly fetcher: Fetcher;

  constructor(private readonly options: AgentApiClientOptions) {
    this.baseUrl = normalizeBaseUrl(options.baseUrl);
    this.fetcher = options.fetcher ?? fetch;
  }

  listReadyDocumentVersions(signal?: AbortSignal): Promise<ReadyDocumentVersion[]> {
    return this.requestJson(
      "/api/agent-runs/ready-document-versions",
      z.array(readyDocumentVersionSchema),
      [200],
      { signal },
    );
  }

  createRun(
    request: CreateAgentRunRequest,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<CreateAgentRunResponse> {
    return this.requestJson(
      "/api/agent-runs",
      createAgentRunResponseSchema,
      [200, 202],
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": validateIdempotencyKey(idempotencyKey),
        },
        body: JSON.stringify(parseOutgoing(createAgentRunRequestSchema, request)),
        signal,
      },
    );
  }

  getRun(runId: string, signal?: AbortSignal): Promise<AgentRunStatusResponse> {
    return this.requestJson(
      `/api/agent-runs/${encodeURIComponent(parseOutgoingStringUuid(runId))}`,
      agentRunStatusResponseSchema,
      [200],
      { signal },
    );
  }

  listEvents(runId: string, afterSequence = 0, signal?: AbortSignal): Promise<AgentRunEventResponse[]> {
    const id = parseOutgoingStringUuid(runId);
    if (!Number.isSafeInteger(afterSequence) || afterSequence < 0) {
      throw new AgentApiProtocolError("Agent event cursor must be a non-negative safe integer.");
    }
    return this.requestJson(
      `/api/agent-runs/${encodeURIComponent(id)}/events?afterSeq=${afterSequence}&limit=500`,
      z.array(agentRunEventResponseSchema),
      [200],
      { signal },
    );
  }

  async openEventStream(runId: string, lastSequence: number, signal?: AbortSignal): Promise<Response> {
    const id = parseOutgoingStringUuid(runId);
    if (!Number.isSafeInteger(lastSequence) || lastSequence < 0) {
      throw new AgentApiProtocolError("Agent event cursor must be a non-negative safe integer.");
    }
    const response = await this.authorizedFetch(`/api/agent-runs/${encodeURIComponent(id)}/events/stream`, {
      headers: { Accept: "text/event-stream", "Last-Event-ID": String(lastSequence) },
      signal,
    });
    if (response.status !== 200) {
      await this.throwResponseError(response);
    }
    return response;
  }

  cancelRun(runId: string, signal?: AbortSignal): Promise<AgentRunStatusResponse> {
    return this.requestJson(
      `/api/agent-runs/${encodeURIComponent(parseOutgoingStringUuid(runId))}/cancel`,
      agentRunStatusResponseSchema,
      [200],
      { method: "POST", signal },
    );
  }

  getApproval(approvalId: string, signal?: AbortSignal): Promise<ApprovalRequestResponse> {
    return this.requestJson(
      `/api/approvals/${encodeURIComponent(parseOutgoingStringUuid(approvalId))}`,
      approvalRequestResponseSchema,
      [200],
      { signal },
    );
  }

  decideApproval(
    approvalId: string,
    request: ApprovalDecisionRequest,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<ApprovalDecisionResponse> {
    return this.requestJson(
      `/api/approvals/${encodeURIComponent(parseOutgoingStringUuid(approvalId))}/decisions`,
      approvalDecisionResponseSchema,
      [200, 202],
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": validateIdempotencyKey(idempotencyKey),
        },
        body: JSON.stringify(parseOutgoing(approvalDecisionRequestSchema, request)),
        signal,
      },
    );
  }

  listArtifacts(runId: string, signal?: AbortSignal): Promise<AgentArtifactResponse[]> {
    return this.requestJson(
      `/api/agent-runs/${encodeURIComponent(parseOutgoingStringUuid(runId))}/artifacts`,
      z.array(agentArtifactResponseSchema),
      [200],
      { signal },
    );
  }

  getArtifactDownload(artifactId: string, signal?: AbortSignal): Promise<AgentArtifactDownloadResponse> {
    return this.requestJson(
      `/api/agent-artifacts/${encodeURIComponent(parseOutgoingStringUuid(artifactId))}/download`,
      agentArtifactDownloadResponseSchema,
      [200],
      { signal },
    );
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
      throw new AgentApiProtocolError("Agent API response does not match its runtime schema.", {
        cause: result.error,
      });
    }
    return result.data;
  }

  private async authorizedFetch(path: string, init: RequestInit = {}): Promise<Response> {
    const token = this.options.getToken();
    if (token === null || token.trim() === "") {
      throw new AgentAuthenticationError();
    }
    const headers = new Headers(init.headers);
    headers.set("Accept", headers.get("Accept") ?? "application/json");
    headers.set("Authorization", `Bearer ${token}`);
    try {
      // Keep the native fetch receiver unbound. Calling `this.fetcher(...)`
      // makes Chromium treat the client instance as `this` and throws
      // `Illegal invocation` before a request is sent.
      const fetcher = this.fetcher;
      const response = await fetcher(`${this.baseUrl}${path}`, { ...init, headers });
      return response;
    } catch (error) {
      if (init.signal?.aborted === true || (error instanceof DOMException && error.name === "AbortError")) {
        throw new AgentNetworkError("aborted", "Agent API request was canceled.", { cause: error });
      }
      throw new AgentNetworkError("network_error", "Agent API request failed.", { cause: error });
    }
  }

  private async throwResponseError(response: Response): Promise<never> {
    const body = await parseJson(response);
    const parsed = errorResponseSchema.safeParse(body);
    if (!parsed.success) {
      throw new AgentApiProtocolError("Agent API error response does not match its runtime schema.", {
        cause: parsed.error,
      });
    }
    throw new AgentApiError(
      response.status,
      parsed.data.error.code,
      parsed.data.error.message,
      parsed.data.error.requestId,
    );
  }
}

function parseOutgoingStringUuid(value: string): string {
  const result = z.string().uuid().safeParse(value);
  if (!result.success) {
    throw new AgentApiProtocolError("Agent resource identifier is invalid.", { cause: result.error });
  }
  return result.data;
}
