import { z, type ZodType } from "zod";

import {
  documentAccessResponseSchema,
  documentGrantCreateRequestSchema,
  documentGrantResponseSchema,
  documentInventoryResponseSchema,
  errorResponseSchema,
  type DocumentAccessMode,
  type DocumentAccessResponse,
  type DocumentGrantCreateRequest,
  type DocumentGrantResponse,
  type DocumentInventoryItem,
} from "../agent/api/schemas";

function normalizeBaseUrl(value: string | undefined): string {
  return (value ?? "").replace(/\/+$/, "");
}

export async function fetchDocumentInventory(
  token: string,
  signal?: AbortSignal,
): Promise<DocumentInventoryItem[]> {
  const response = await fetch(`${normalizeBaseUrl(import.meta.env.VITE_API_BASE_URL)}/api/documents?limit=200`, {
    headers: { Accept: "application/json", Authorization: `Bearer ${token}` },
    signal,
  });
  if (!response.ok) {
    const parsed = errorResponseSchema.safeParse(await response.json().catch(() => null));
    throw new DocumentApiError(
      response.status,
      parsed.success ? parsed.data.error.code : "document_inventory_request_failed",
      parsed.success ? parsed.data.error.message : `Document inventory request failed (${response.status}).`,
      parsed.success ? parsed.data.error.requestId : null,
    );
  }
  const parsed = documentInventoryResponseSchema.safeParse(await response.json());
  if (!parsed.success) {
    throw new Error("Document inventory response schema is invalid.");
  }
  return parsed.data;
}

export class DocumentApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly requestId: string | null = null,
  ) {
    super(message);
    this.name = "DocumentApiError";
  }
}

function documentPath(documentId: string): string {
  return `/api/documents/${encodeURIComponent(z.string().uuid().parse(documentId))}`;
}

async function requestJson<T>(
  token: string,
  path: string,
  schema: ZodType<T>,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${normalizeBaseUrl(import.meta.env.VITE_API_BASE_URL)}${path}`, {
    ...init,
    headers,
  });
  if (!response.ok) {
    const parsed = errorResponseSchema.safeParse(await response.json().catch(() => null));
    throw new DocumentApiError(
      response.status,
      parsed.success ? parsed.data.error.code : "document_request_failed",
      parsed.success ? parsed.data.error.message : `Document request failed (${response.status}).`,
      parsed.success ? parsed.data.error.requestId : null,
    );
  }
  const parsed = schema.safeParse(await response.json());
  if (!parsed.success) throw new Error("Document response schema is invalid.");
  return parsed.data;
}

export function fetchDocumentAccess(
  token: string,
  documentId: string,
  signal?: AbortSignal,
): Promise<DocumentAccessResponse> {
  return requestJson(token, `${documentPath(documentId)}/access`, documentAccessResponseSchema, { signal });
}

export function updateDocumentAccess(
  token: string,
  documentId: string,
  accessMode: DocumentAccessMode,
  signal?: AbortSignal,
): Promise<DocumentAccessResponse> {
  return requestJson(token, `${documentPath(documentId)}/access`, documentAccessResponseSchema, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ accessMode }),
    signal,
  });
}

export function fetchDocumentGrants(
  token: string,
  documentId: string,
  signal?: AbortSignal,
): Promise<DocumentGrantResponse[]> {
  return requestJson(token, `${documentPath(documentId)}/grants`, z.array(documentGrantResponseSchema), { signal });
}

export function createDocumentGrant(
  token: string,
  documentId: string,
  request: DocumentGrantCreateRequest,
  signal?: AbortSignal,
): Promise<DocumentGrantResponse> {
  const payload = documentGrantCreateRequestSchema.parse(request);
  return requestJson(token, `${documentPath(documentId)}/grants`, documentGrantResponseSchema, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
}

export async function deleteDocumentGrant(
  token: string,
  documentId: string,
  grantId: string,
  signal?: AbortSignal,
): Promise<void> {
  const path = `${documentPath(documentId)}/grants/${encodeURIComponent(z.string().uuid().parse(grantId))}`;
  const response = await fetch(`${normalizeBaseUrl(import.meta.env.VITE_API_BASE_URL)}${path}`, {
    method: "DELETE",
    headers: { Accept: "application/json", Authorization: `Bearer ${token}` },
    signal,
  });
  if (!response.ok) {
    const parsed = errorResponseSchema.safeParse(await response.json().catch(() => null));
    throw new DocumentApiError(
      response.status,
      parsed.success ? parsed.data.error.code : "document_request_failed",
      parsed.success ? parsed.data.error.message : `Document request failed (${response.status}).`,
      parsed.success ? parsed.data.error.requestId : null,
    );
  }
}
