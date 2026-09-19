import { expect, type BrowserContext, type Page, type TestInfo } from "@playwright/test";
import { writeFile } from "node:fs/promises";
import type { z } from "zod";
import type { Snapshot } from "./pipeline";
import { business, privacyAndLayout, session, switchEnterprise } from "./support";
import { usageEvidenceSchema, usagePage } from "./usage";

export async function switchWithDelayedUsage(page: Page, context: BrowserContext, state: Snapshot, a: { name: string; id: string }, b: { name: string; id: string }, info: TestInfo) {
  await switchEnterprise(page, a.name);
  await usagePage(page, a.id, state);
  const other = await context.newPage();
  await usagePage(other, a.id, state);
  const stale = await session(page);
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  let captured: z.infer<typeof usageEvidenceSchema> | undefined;
  let intercepted = false;
  let aborted = false;
  let fulfillment: "pending" | "fulfilled" | "rejected" = "pending";
  page.on("requestfailed", request => {
    if (new URL(request.url()).pathname === "/api/tenant-usage" && request.failure()?.errorText === "net::ERR_ABORTED") aborted = true;
  });
  await page.route("**/api/tenant-usage", async route => {
    if (intercepted) { await route.continue(); return; }
    intercepted = true;
    const response = await route.fetch();
    expect(response.ok()).toBe(true);
    captured = usageEvidenceSchema.parse(await response.json());
    await held;
    try {
      await route.fulfill({ response });
      fulfillment = "fulfilled";
    } catch {
      // The following aborted assertion distinguishes retirement from other failures.
      fulfillment = "rejected";
    }
  });
  try {
    await page.getByRole("button", { name: "刷新用量", exact: true }).click();
    await expect.poll(() => captured?.tenantId).toBe(a.id);
    await switchEnterprise(page, b.name);
    await expect(other.locator(".session-identity")).toContainText(b.name);
  } finally {
    release();
    await page.unrouteAll({ behavior: "wait" });
  }
  expect(captured?.providerRequestsUsed).toBe(4);
  await expect.poll(() => aborted).toBe(true);
  for (const target of [page, other]) {
    await expect(target.locator(".usage-generation-metrics dd")).toHaveText(["0", "1", "0", "1"]);
    await privacyAndLayout(target);
  }
  const staleRead = await business(page, "/api/tenant-usage", { credential: stale });
  expect(staleRead.status).toBe(409);
  expect(staleRead.body).toMatchObject({ error: { code: "browser_context_stale" } });
  await page.screenshot({ path: info.outputPath("desktop-switch-race.png"), fullPage: true });
  await writeFile(info.outputPath("switch-evidence.json"), JSON.stringify({ captured, aborted, fulfillment, staleRead, secondTabTenant: (await session(other)).currentTenant?.tenantId }, null, 2) + "\n");
  return other;
}
