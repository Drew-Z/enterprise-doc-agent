import { authenticatedFetch, type ApiCredential } from "../auth/transport";
import { z, type ZodType } from "zod";

import { errorResponseSchema } from "../agent/api/schemas";

const uuidSchema = z.string().uuid();
const roleSchema = z.enum(["owner", "member"]);

export const tenantMemberSchema = z.object({
  membershipId: uuidSchema,
  tenantId: uuidSchema,
  userId: uuidSchema,
  email: z.string().email(),
  role: roleSchema,
  isActive: z.boolean(),
  createdAt: z.iso.datetime({ offset: true }),
  updatedAt: z.iso.datetime({ offset: true }),
}).strict();

const provisionMemberSchema = z.object({
  email: z.string().trim().email().max(320),
  role: roleSchema,
}).strict();

export type TenantMember = z.infer<typeof tenantMemberSchema>;
export type TenantMemberRole = z.infer<typeof roleSchema>;

function normalizeBaseUrl(value: string | undefined): string {
  return (value ?? "").replace(/\/+$/, "");
}

export class MembersApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly requestId: string | null = null,
  ) {
    super(message);
    this.name = "MembersApiError";
  }
}

async function requestJson<T>(
  token: ApiCredential,
  path: string,
  schema: ZodType<T>,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  const response = await authenticatedFetch(`${normalizeBaseUrl(import.meta.env.VITE_API_BASE_URL)}${path}`, token, {
    ...init,
    headers,
  });
  if (!response.ok) {
    const parsed = errorResponseSchema.safeParse(await response.json().catch(() => null));
    throw new MembersApiError(
      response.status,
      parsed.success ? parsed.data.error.code : "membership_administration_request_failed",
      parsed.success ? parsed.data.error.message : `Membership request failed (${response.status}).`,
      parsed.success ? parsed.data.error.requestId : null,
    );
  }
  const parsed = schema.safeParse(await response.json());
  if (!parsed.success) throw new Error("Membership response schema is invalid.");
  return parsed.data;
}

export function fetchTenantMembers(
  token: ApiCredential,
  query = "",
  signal?: AbortSignal,
): Promise<TenantMember[]> {
  const normalizedQuery = query.trim();
  const path = normalizedQuery ? `/api/members?q=${encodeURIComponent(normalizedQuery)}` : "/api/members";
  return requestJson(token, path, z.array(tenantMemberSchema), { signal });
}

export function provisionTenantMember(
  token: ApiCredential,
  email: string,
  role: TenantMemberRole,
): Promise<TenantMember> {
  const payload = provisionMemberSchema.parse({ email, role });
  return requestJson(token, "/api/members", tenantMemberSchema, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function changeTenantMemberRole(
  token: ApiCredential,
  membershipId: string,
  role: TenantMemberRole,
): Promise<TenantMember> {
  const path = `/api/members/${encodeURIComponent(uuidSchema.parse(membershipId))}/role`;
  return requestJson(token, path, tenantMemberSchema, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role }),
  });
}

export function deactivateTenantMember(token: ApiCredential, membershipId: string): Promise<TenantMember> {
  const path = `/api/members/${encodeURIComponent(uuidSchema.parse(membershipId))}`;
  return requestJson(token, path, tenantMemberSchema, { method: "DELETE" });
}

export function activateTenantMember(token: ApiCredential, membershipId: string): Promise<TenantMember> {
  const path = `/api/members/${encodeURIComponent(uuidSchema.parse(membershipId))}/activate`;
  return requestJson(token, path, tenantMemberSchema, { method: "POST" });
}
