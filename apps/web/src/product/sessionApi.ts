import { authenticatedFetch, type ApiCredential } from "../auth/transport";
import { z } from "zod";

import { errorResponseSchema } from "../agent/api/schemas";

const sessionSchema = z.object({
  tenantId: z.string().uuid(),
  actorId: z.string().uuid(),
  role: z.enum(["owner", "member"]),
  capabilities: z.object({
    documentRead: z.boolean(),
    documentWrite: z.boolean(),
    agentRunCreate: z.boolean(),
    auditRead: z.boolean(),
    auditExport: z.boolean(),
    approvalDecide: z.boolean(),
  }),
});

export type ProductSession = z.infer<typeof sessionSchema>;

export class ProductSessionApiError extends Error {
  constructor(readonly status: number, message: string, readonly requestId: string | null = null) {
    super(message);
    this.name = "ProductSessionApiError";
  }
}

const logoutSchema = z.object({
  revoked: z.boolean(),
  alreadyRevoked: z.boolean(),
  revokedAt: z.iso.datetime({ offset: true }),
});

function normalizeBaseUrl(value: string | undefined): string {
  return (value ?? "").replace(/\/+$/, "");
}

export async function fetchProductSession(token: ApiCredential, signal?: AbortSignal): Promise<ProductSession> {
  const response = await authenticatedFetch(`${normalizeBaseUrl(import.meta.env.VITE_API_BASE_URL)}/api/session`, token, {
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) {
    const parsed = errorResponseSchema.safeParse(await response.json().catch(() => null));
    throw new ProductSessionApiError(
      response.status,
      parsed.success ? parsed.data.error.message : `Session request failed (${response.status}).`,
      parsed.success ? parsed.data.error.requestId : null,
    );
  }
  const parsed = sessionSchema.safeParse(await response.json());
  if (!parsed.success) throw new Error("Session response schema is invalid.");
  return parsed.data;
}

export async function logoutProductSession(token: string, signal?: AbortSignal): Promise<void> {
  const response = await authenticatedFetch(`${normalizeBaseUrl(import.meta.env.VITE_API_BASE_URL)}/api/session/logout`, token, {
    method: "POST",
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) throw new Error(`Logout request failed (${response.status}).`);
  const parsed = logoutSchema.safeParse(await response.json());
  if (!parsed.success) throw new Error("Logout response schema is invalid.");
}
