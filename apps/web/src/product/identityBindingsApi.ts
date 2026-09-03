import { z, type ZodType } from "zod";

import { errorResponseSchema } from "../agent/api/schemas";

const uuidSchema = z.string().uuid();

export const identityBindingSchema = z.object({
  bindingId: uuidSchema,
  tenantId: uuidSchema,
  issuer: z.string().min(1).max(512),
  subject: z.string().min(1).max(512),
  userId: uuidSchema,
  userEmail: z.string().email(),
  isActive: z.boolean(),
  createdAt: z.iso.datetime({ offset: true }),
  updatedAt: z.iso.datetime({ offset: true }),
}).strict();

export const identityBindingCreateRequestSchema = z.object({
  issuer: z.string().trim().min(1).max(512),
  subject: z.string().trim().min(1).max(512),
  userId: uuidSchema,
}).strict();

export const identityMemberSchema = z.object({
  userId: uuidSchema,
  email: z.string().email(),
  role: z.enum(["owner", "member"]),
}).strict();

export type IdentityBinding = z.infer<typeof identityBindingSchema>;
export type IdentityBindingCreateRequest = z.infer<typeof identityBindingCreateRequestSchema>;
export type IdentityMember = z.infer<typeof identityMemberSchema>;

function normalizeBaseUrl(value: string | undefined): string {
  return (value ?? "").replace(/\/+$/, "");
}

export class IdentityBindingApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly requestId: string | null = null,
  ) {
    super(message);
    this.name = "IdentityBindingApiError";
  }
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
    throw new IdentityBindingApiError(
      response.status,
      parsed.success ? parsed.data.error.code : "external_identity_binding_request_failed",
      parsed.success ? parsed.data.error.message : `Identity binding request failed (${response.status}).`,
      parsed.success ? parsed.data.error.requestId : null,
    );
  }
  const parsed = schema.safeParse(await response.json());
  if (!parsed.success) throw new Error("Identity binding response schema is invalid.");
  return parsed.data;
}

export function fetchIdentityBindings(token: string, signal?: AbortSignal): Promise<IdentityBinding[]> {
  return requestJson(token, "/api/identity-bindings", z.array(identityBindingSchema), { signal });
}

export function fetchIdentityMembers(
  token: string,
  query = "",
  signal?: AbortSignal,
): Promise<IdentityMember[]> {
  const normalizedQuery = query.trim();
  const path = normalizedQuery
    ? `/api/identity-bindings/members?q=${encodeURIComponent(normalizedQuery)}`
    : "/api/identity-bindings/members";
  return requestJson(token, path, z.array(identityMemberSchema), { signal });
}

export function createIdentityBinding(
  token: string,
  request: IdentityBindingCreateRequest,
  signal?: AbortSignal,
): Promise<IdentityBinding> {
  const payload = identityBindingCreateRequestSchema.parse(request);
  return requestJson(token, "/api/identity-bindings", identityBindingSchema, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
}

export function deactivateIdentityBinding(
  token: string,
  bindingId: string,
  signal?: AbortSignal,
): Promise<IdentityBinding> {
  const path = `/api/identity-bindings/${encodeURIComponent(uuidSchema.parse(bindingId))}`;
  return requestJson(token, path, identityBindingSchema, { method: "DELETE", signal });
}

export function activateIdentityBinding(
  token: string,
  bindingId: string,
  signal?: AbortSignal,
): Promise<IdentityBinding> {
  const path = `/api/identity-bindings/${encodeURIComponent(uuidSchema.parse(bindingId))}/activate`;
  return requestJson(token, path, identityBindingSchema, { method: "POST", signal });
}
