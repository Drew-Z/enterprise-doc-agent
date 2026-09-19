import { z } from "zod";

import { errorResponseSchema } from "../agent/api/schemas";
import { authenticatedFetch, type ApiCredential } from "../auth/transport";

const count = z.number().int().min(0).max(Number.MAX_SAFE_INTEGER);
const date = z.iso.datetime({ offset: true });
const resourcesSchema = z.object({
  storageLimitBytes: count.positive(),
  storageUsedBytes: count,
  storageReservedBytes: count,
  storageRemainingBytes: count,
  seatsUsed: count,
  seatLimit: count.positive().nullable(),
  seatsRemaining: count.nullable(),
}).strict().refine(value =>
  value.storageUsedBytes <= value.storageLimitBytes - value.storageReservedBytes
  && value.storageRemainingBytes === value.storageLimitBytes - value.storageUsedBytes - value.storageReservedBytes
  && value.seatsRemaining === (value.seatLimit === null ? null : Math.max(0, value.seatLimit - value.seatsUsed)),
{ message: "Resource totals are inconsistent." });

const eventSchema = z.object({
  eventType: z.enum(["consume", "release"]),
  quantity: count.positive(),
  operationId: z.string().uuid(),
  provider: z.string().nullable(),
  model: z.string().nullable(),
  totalTokens: count.nullable(),
  // Pydantic serializes Decimal as a string; PostgreSQL zero may be "0E-8".
  estimatedCost: z.string().max(80).regex(/^\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$/).nullable(),
  currency: z.string().nullable(),
  pricingVersion: z.string().nullable(),
  source: z.string(),
  occurredAt: date,
}).strict();

const tenantUsageSchema = z.object({
  tenantId: z.string().uuid(),
  enabled: z.boolean(),
  entitlementStatus: z.enum(["legacy", "active", "inactive"]),
  planCode: z.string().min(1).nullable(),
  version: count.positive().nullable(),
  periodStart: date.nullable(),
  periodEnd: date.nullable(),
  providerRequestLimit: count.nullable(),
  providerRequestsUsed: count,
  providerRequestsReserved: count,
  providerRequestsRemaining: count.nullable(),
  costStatus: z.enum(["known", "unknown"]),
  recentEvents: z.array(eventSchema).max(20),
  resources: resourcesSchema,
}).strict().refine(value => {
  if (value.entitlementStatus === "active") {
    return value.enabled && value.planCode !== null && value.version !== null
      && value.periodStart !== null && value.periodEnd !== null
      && Date.parse(value.periodEnd) > Date.parse(value.periodStart)
      && value.providerRequestsRemaining === (value.providerRequestLimit === null ? null
        : Math.max(0, value.providerRequestLimit - value.providerRequestsUsed - value.providerRequestsReserved));
  }
  return !value.enabled && value.planCode === null && value.version === null
    && value.periodStart === null && value.periodEnd === null && value.providerRequestLimit === null
    && value.providerRequestsUsed === 0 && value.providerRequestsReserved === 0
    && value.providerRequestsRemaining === (value.entitlementStatus === "inactive" ? 0 : null)
    && value.costStatus === "unknown" && value.recentEvents.length === 0;
}, { message: "Entitlement state is inconsistent." });

export type TenantUsage = z.infer<typeof tenantUsageSchema>;

export class TenantUsageApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly requestId: string | null = null,
  ) {
    super(message);
    this.name = "TenantUsageApiError";
  }
}

export async function fetchTenantUsage(
  credential: ApiCredential,
  expectedTenantId: string,
  signal?: AbortSignal,
): Promise<TenantUsage> {
  if (typeof credential !== "string" && credential.tenantId !== expectedTenantId) {
    throw new TenantUsageApiError(0, "tenant_usage_context_mismatch", "The enterprise session has changed.");
  }
  const baseUrl = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/+$/, "");
  const response = await authenticatedFetch(`${baseUrl}/api/tenant-usage`, credential, {
    headers: { Accept: "application/json" }, signal,
  });
  const body: unknown = await response.json().catch(() => null);
  // A switch can happen while the response body is still being read.
  signal?.throwIfAborted();
  if (typeof credential !== "string") credential.signal.throwIfAborted();
  const requestId = response.headers.get("X-Request-Id");
  if (!response.ok) {
    const error = errorResponseSchema.safeParse(body);
    throw new TenantUsageApiError(
      response.status,
      error.success ? error.data.error.code : "tenant_usage_request_failed",
      error.success ? error.data.error.message : "Enterprise usage could not be loaded.",
      error.success ? error.data.error.requestId : requestId,
    );
  }
  const parsed = tenantUsageSchema.safeParse(body);
  if (!parsed.success) {
    throw new TenantUsageApiError(response.status, "tenant_usage_invalid_response", "Enterprise usage data could not be read accurately.", requestId);
  }
  if (parsed.data.tenantId !== expectedTenantId) {
    throw new TenantUsageApiError(response.status, "tenant_usage_context_mismatch", "The enterprise session has changed.", requestId);
  }
  return parsed.data;
}
