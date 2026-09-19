import { expect, type Page } from "@playwright/test";
import { z } from "zod";
import type { Snapshot } from "./pipeline";

// A test projection of the actual response. Product decoding remains in tenantUsageApi.
export const usageEvidenceSchema = z.object({
  tenantId: z.string().uuid(), enabled: z.boolean(), entitlementStatus: z.string(),
  planCode: z.string().nullable(), providerRequestLimit: z.number().nullable(),
  providerRequestsUsed: z.number(), providerRequestsReserved: z.number(), providerRequestsRemaining: z.number().nullable(),
  costStatus: z.string(),
  resources: z.object({ storageLimitBytes: z.number(), storageUsedBytes: z.number(), storageReservedBytes: z.number(), storageRemainingBytes: z.number(), seatsUsed: z.number(), seatLimit: z.number().nullable(), seatsRemaining: z.number().nullable() }),
  recentEvents: z.array(z.object({ operationId: z.string().uuid(), eventType: z.string(), quantity: z.number(), estimatedCost: z.string().nullable(), currency: z.string().nullable() })),
});

export async function usagePage(page: Page, tenantId: string, state: Snapshot) {
  const refresh = new URL(page.url()).hash === "#/usage";
  if (refresh) await expect(page.getByRole("button", { name: "刷新用量", exact: true })).toBeEnabled();
  const received = page.waitForResponse(response => new URL(response.url()).pathname === "/api/tenant-usage" && response.request().method() === "GET");
  if (refresh) await page.getByRole("button", { name: "刷新用量", exact: true }).click();
  else await page.goto("/#/usage");
  const response = await received;
  expect(response.ok()).toBe(true);
  const data = usageEvidenceSchema.parse(await response.json());
  const period = state.entitlements.find(item => item.tenantId === tenantId);
  const tenant = state.tenants.find(item => item.id === tenantId);
  if (!period || !tenant || period.limit === null) throw new Error("Expected this run's bounded enterprise period.");
  const seats = state.memberships.filter(item => item.tenantId === tenantId && item.isActive).length;
  const expectedEvents = state.usageEvents.filter(item => item.tenantId === tenantId);
  expect(data).toMatchObject({ tenantId, enabled: true, entitlementStatus: "active", planCode: "synthetic-first-use", providerRequestLimit: period.limit, providerRequestsUsed: period.used, providerRequestsReserved: period.reserved, providerRequestsRemaining: period.limit - period.used - period.reserved, costStatus: "unknown" });
  expect(data.resources).toEqual({ storageLimitBytes: tenant.storageLimitBytes, storageUsedBytes: tenant.storageUsedBytes, storageReservedBytes: tenant.storageReservedBytes, storageRemainingBytes: tenant.storageLimitBytes - tenant.storageUsedBytes - tenant.storageReservedBytes, seatsUsed: seats, seatLimit: 2, seatsRemaining: 2 - seats });
  expect(data.recentEvents.map(item => item.operationId).sort()).toEqual(expectedEvents.map(item => item.operationId).sort());
  expect(data.recentEvents.every(item => item.estimatedCost === null && item.currency === null)).toBe(true);
  const metrics = page.locator(".usage-generation-metrics");
  for (const [label, value] of [["已使用", period.used], ["处理中预留", period.reserved], ["可用", period.limit - period.used - period.reserved], ["周期上限", period.limit]] as const) {
    await expect(metrics.locator(".usage-metric").filter({ has: page.locator("dt", { hasText: new RegExp("^" + label + "$") }) }).locator("dd")).toHaveText(String(value));
  }
  await expect(page.locator(".usage-seat-metrics .usage-metric").filter({ hasText: "活跃成员" }).locator("dd")).toHaveText(String(seats));
  await expect(page.locator(".usage-cost-note")).toContainText("模型费用未知");
  await expect(page.locator(".usage-events .usage-event")).toHaveCount(expectedEvents.length);
  const storageUsed = page.locator(".usage-resource").filter({ has: page.getByRole("heading", { name: "存储空间" }) }).locator(".usage-metric").filter({ hasText: "已使用" }).locator("dd");
  await expect(storageUsed).toHaveAttribute("title", new Intl.NumberFormat("zh-CN").format(tenant.storageUsedBytes) + " bytes");
  return data;
}
