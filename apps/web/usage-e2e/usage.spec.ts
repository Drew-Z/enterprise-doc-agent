import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import path from "node:path";

import type { BrowserTenant } from "../src/auth/schemas";
import { tenantUsage, usageActor, usageTenantA, usageTenantB, usageWithoutPeriod } from "../src/test/tenantUsage";

async function mockWorkspace(page: Page, role: "owner" | "member" = "owner") {
  const tenants: BrowserTenant[] = [
    { tenantId: usageTenantA, actorId: usageActor, name: "澄明科技", role },
    { tenantId: usageTenantB, actorId: usageActor, name: "远山咨询", role: "owner" },
  ];
  const state = {
    current: tenants[0], authenticated: true, revision: 1, usageRequests: 0,
    usageStatus: 200, usage: tenantUsage(), delayNext: false,
    releaseDelayed: () => { /* Replaced when a response is deliberately delayed. */ },
  };
  const browserSession = () => ({
    status: "authenticated", email: "owner@example.test", expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
    contextVersion: `${"a".repeat(32)}.${state.revision}`, csrfToken: "c".repeat(64), currentTenant: state.current,
  });
  const failures: string[] = [];
  page.on("pageerror", error => failures.push(error.message));
  await page.addInitScript(() => localStorage.setItem("enterprise-doc-agent.locale", "zh"));
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.origin !== "http://127.0.0.1:5187") {
      failures.push(`Unexpected external request: ${url.origin}${url.pathname}`);
      await route.abort();
      return;
    }
    const pathname = url.pathname;
    const json = (value: unknown, status = 200) => route.fulfill({ status, json: value, headers: { "Cache-Control": "no-store" } });
    if (pathname === "/auth/session") return json(state.authenticated ? browserSession() : { status: "anonymous" });
    if (pathname === "/auth/tenants") return json(tenants);
    if (pathname === "/auth/tenant") {
      const payload: unknown = route.request().postDataJSON();
      const tenantId = typeof payload === "object" && payload !== null && "tenantId" in payload ? payload.tenantId : null;
      const next = tenants.find(tenant => tenant.tenantId === tenantId);
      if (!next) throw new Error("Unknown test enterprise");
      expect(route.request().headers()["x-csrf-token"]).toBe("c".repeat(64));
      state.current = next;
      state.revision += 1;
      return json(browserSession());
    }
    if (pathname === "/auth/logout") { state.authenticated = false; return json({ revoked: true }); }
    if (pathname === "/health/ready") return json({ status: "ready", checks: { database: { status: "up" }, redis: { status: "up" }, object_store: { status: "up" } } });
    if (pathname === "/api/session") return json({
      tenantId: state.current.tenantId, actorId: state.current.actorId, role: state.current.role,
      capabilities: { documentRead: true, documentWrite: true, agentRunCreate: true, auditRead: true, auditExport: true, approvalDecide: true },
    });
    if (pathname === "/api/tenant-usage") {
      state.usageRequests += 1;
      expect(route.request().headers()["authorization"]).toBeUndefined();
      expect(route.request().headers()["x-session-context"]).toBe(`${"a".repeat(32)}.${state.revision}`);
      const snapshot = state.current.tenantId === usageTenantA ? state.usage : tenantUsage({ tenantId: usageTenantB, planCode: "second-enterprise" });
      if (state.delayNext) {
        state.delayNext = false;
        await new Promise<void>(resolve => { state.releaseDelayed = resolve; });
      }
      return state.usageStatus === 200 ? json(snapshot)
        : json({ error: { code: state.usageStatus === 403 ? "tenant_usage_forbidden" : "usage_store_unavailable", message: "Usage unavailable", requestId: "req-browser-usage" } }, state.usageStatus);
    }
    if (pathname.startsWith("/api/") || pathname.startsWith("/auth/")) {
      failures.push(`Unexpected application request: ${pathname}`);
      return json({ error: "Unexpected test request" }, 500);
    }
    return route.continue();
  });
  return { state, tenants, failures };
}

async function assertFits(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
}

async function screenshot(page: Page, info: TestInfo, name: string) {
  const directory = process.env.TENANT_USAGE_E2E_ARTIFACT_DIR;
  const target = directory ? path.join(directory, name) : info.outputPath(name);
  await mkdir(path.dirname(target), { recursive: true });
  await page.screenshot({ path: target, fullPage: true, animations: "disabled" });
}

test("desktop owner usage, failed refresh, recovery and all period states", async ({ page }, info) => {
  const { state, failures } = await mockWorkspace(page);
  await page.goto("/#/overview");
  await page.getByRole("button", { name: "企业用量", exact: true }).click();
  await expect(page.getByText("team-pilot", { exact: true })).toBeVisible();
  await expect(page.getByText("模型费用未知", { exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "存储空间" })).toContainText("640 MiB");
  await assertFits(page);
  await screenshot(page, info, "usage-desktop.png");

  state.usageStatus = 503;
  await page.getByRole("button", { name: "刷新用量" }).click();
  await expect(page.getByRole("alert")).toContainText("req-browser-usage");
  await expect(page.getByRole("region", { name: "售前生成额度" })).toHaveCount(0);

  state.usageStatus = 200;
  for (const status of ["legacy", "inactive"] as const) {
    state.usage = usageWithoutPeriod(status);
    await page.getByRole("button", { name: "刷新用量" }).click();
    await expect(page.getByText(status === "legacy" ? "尚未配置生成周期" : "当前没有生效中的生成周期", { exact: true })).toBeVisible();
    await expect(page.getByRole("region", { name: "存储空间" })).toContainText("256 MiB");
  }
  state.usage = tenantUsage({ costStatus: "known" });
  state.usage.recentEvents[0].estimatedCost = "0.01234567";
  state.usage.recentEvents[0].currency = "USD";
  await page.getByRole("button", { name: "刷新用量" }).click();
  await expect(page.getByText("0.01234567 USD", { exact: true })).toBeVisible();
  state.usageStatus = 403;
  await page.getByRole("button", { name: "刷新用量" }).click();
  await expect(page.getByRole("alert")).toContainText("仅企业管理员");
  await expect(page.getByText("0.01234567 USD", { exact: true })).toHaveCount(0);
  expect(failures).toEqual([]);
});

test("mobile navigation, keyboard refresh and Chinese/English layout", async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const { state, failures } = await mockWorkspace(page);
  await page.goto("/#/overview");
  await page.getByRole("button", { name: "打开导航" }).click();
  await page.getByRole("button", { name: "企业用量", exact: true }).click();
  await expect(page.getByText("team-pilot", { exact: true })).toBeVisible();
  await assertFits(page);
  await screenshot(page, info, "usage-mobile.png");
  const before = state.usageRequests;
  await page.getByRole("button", { name: "刷新用量" }).focus();
  await page.keyboard.press("Enter");
  await expect.poll(() => state.usageRequests).toBe(before + 1);
  await expect(page.getByText("team-pilot", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "语言", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Enterprise usage", exact: true })).toBeVisible();
  await assertFits(page);
  expect(failures).toEqual([]);
});

test("browser enterprise switching fences late usage, role loss and logout", async ({ page }) => {
  const { state, tenants, failures } = await mockWorkspace(page);
  await page.goto("/#/usage");
  await expect(page.getByText("team-pilot", { exact: true })).toBeVisible();
  state.delayNext = true;
  const before = state.usageRequests;
  await page.getByRole("button", { name: "刷新用量" }).click();
  await expect.poll(() => state.usageRequests).toBe(before + 1);
  await page.getByRole("button", { name: "切换企业" }).click();
  await page.getByRole("button", { name: "进入 远山咨询" }).click();
  await expect(page.getByText("second-enterprise", { exact: true })).toBeVisible();
  state.releaseDelayed();
  await expect(page.getByText("team-pilot", { exact: true })).toHaveCount(0);
  tenants[1].role = "member";
  await page.evaluate(() => window.dispatchEvent(new Event("pageshow")));
  await expect(page.getByText("仅企业管理员可以查看用量。", { exact: true })).toBeVisible();
  await expect(page.getByText("second-enterprise", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "退出会话", exact: true }).click();
  await expect(page.getByRole("link", { name: "使用企业账号登录" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "企业用量", exact: true })).toHaveCount(0);
  expect(failures).toEqual([]);
});

test("member and showcase direct links never request live usage", async ({ page }) => {
  const { state, failures } = await mockWorkspace(page, "member");
  await page.goto("/#/usage");
  await expect(page.getByText("仅企业管理员可以查看用量。", { exact: true })).toBeVisible();
  expect(state.usageRequests).toBe(0);
  await page.goto("/?showcase=1#/usage");
  await expect(page.getByText("用量仅在已登录的企业中提供。展示模式没有实时用量数据。", { exact: true })).toBeVisible();
  expect(state.usageRequests).toBe(0);
  expect(failures).toEqual([]);
});
