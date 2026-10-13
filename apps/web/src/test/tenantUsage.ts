import type { TenantUsage } from "../product/tenantUsageApi";

export const usageTenantA = "10000000-0000-4000-8000-000000000001";
export const usageTenantB = "20000000-0000-4000-8000-000000000001";
export const usageActor = "10000000-0000-4000-8000-000000000002";

export function tenantUsage(overrides: Partial<TenantUsage> = {}): TenantUsage {
  return {
    tenantId: usageTenantA,
    enabled: true,
    entitlementStatus: "active",
    planCode: "team-pilot",
    version: 1,
    periodStart: "2026-09-01T00:00:00Z",
    periodEnd: "2026-10-01T00:00:00Z",
    providerRequestLimit: 100,
    providerRequestsUsed: 24,
    providerRequestsReserved: 6,
    providerRequestsRemaining: 70,
    costStatus: "unknown",
    modelCalls: { calls: 5, unresolvedCalls: 1, unknownCostCalls: 5, usageKnownCalls: 3, knownTotalTokens: 128 },
    productQuotas: [
      { metric: "agent_task", limit: 20, used: 3, reserved: 2, remaining: 15 },
      { metric: "document_bytes", limit: 10485760, used: 2097152, reserved: 1048576, remaining: 7340032 },
    ],
    recentEvents: [{
      eventType: "consume", quantity: 1, operationId: "10000000-0000-4000-8000-000000000003",
      provider: "test-provider", model: "test-model", totalTokens: null, estimatedCost: null,
      currency: null, pricingVersion: null, source: "presales", occurredAt: "2026-09-15T08:30:00Z",
    }],
    resources: {
      storageLimitBytes: 1073741824, storageUsedBytes: 268435456, storageReservedBytes: 134217728,
      storageRemainingBytes: 671088640, seatsUsed: 3, seatLimit: 5, seatsRemaining: 2,
    },
    ...overrides,
  };
}

export function usageWithoutPeriod(status: "legacy" | "inactive"): TenantUsage {
  return tenantUsage({
    enabled: false, entitlementStatus: status, planCode: null, version: null,
    periodStart: null, periodEnd: null, providerRequestLimit: null,
    providerRequestsUsed: 0, providerRequestsReserved: 0,
    providerRequestsRemaining: status === "inactive" ? 0 : null,
    recentEvents: [], costStatus: "unknown", productQuotas: [],
    modelCalls: { calls: 0, unresolvedCalls: 0, unknownCostCalls: 0, usageKnownCalls: 0, knownTotalTokens: 0 },
  });
}
