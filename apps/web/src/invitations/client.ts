import { z, type ZodType } from "zod";
import { errorResponseSchema } from "../agent/api/schemas";
import { authenticatedFetch, type ApiCredential } from "../auth/transport";
import { invitationListSchema, invitationMutationSchema, type Invitation } from "./schemas";

export class InvitationsApiError extends Error {
  constructor(readonly status: number, readonly code: string, readonly requestId: string | null = null) {
    super("The invitation request could not be confirmed.");
    this.name = "InvitationsApiError";
  }
}

async function request<T>(credential: ApiCredential, path: string, schema: ZodType<T>, init: RequestInit): Promise<T> {
  const signals = [AbortSignal.timeout(20_000)];
  if (init.signal) signals.push(init.signal);
  if (typeof credential !== "string") signals.push(credential.signal);
  const signal = AbortSignal.any(signals);
  const response = await authenticatedFetch(path, credential, {
    ...init, signal, headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}) },
  });
  const body: unknown = await response.json().catch(() => null);
  signal.throwIfAborted();
  if (!response.ok) {
    const error = errorResponseSchema.safeParse(body);
    throw new InvitationsApiError(response.status, error.success ? error.data.error.code : "invitation_request_failed", error.success ? error.data.error.requestId : null);
  }
  const parsed = schema.safeParse(body);
  if (!parsed.success) throw new InvitationsApiError(response.status, "invitation_response_invalid");
  return parsed.data;
}

export function fetchInvitations(credential: ApiCredential, signal: AbortSignal) {
  return request(credential, "/api/invitations", invitationListSchema, { signal });
}

export function createInvitation(credential: ApiCredential, email: string, operationId: string, signal: AbortSignal) {
  const body = z.object({ email: z.string().trim().email().max(320), operationId: z.string().uuid() }).strict().parse({ email, operationId });
  return request(credential, "/api/invitations", invitationMutationSchema, { method: "POST", body: JSON.stringify(body), signal });
}

export function changeInvitation(credential: ApiCredential, invitation: Invitation, action: "regenerate" | "revoke", operationId: string, signal: AbortSignal) {
  const id = z.string().uuid().parse(invitation.invitationId);
  const body = z.object({ expectedGeneration: z.number().int().positive(), operationId: z.string().uuid() }).strict().parse({ expectedGeneration: invitation.generation, operationId });
  return request(credential, `/api/invitations/${id}/${action}`, invitationMutationSchema, { method: "POST", body: JSON.stringify(body), signal });
}
