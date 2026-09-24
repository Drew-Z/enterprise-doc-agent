import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

const api = "http://127.0.0.1:18765";
const testHeaders = { "X-Presales-Test": "presales-browser" };

test.beforeEach(async ({ page, request }) => {
  expect((await request.post(api + "/__presales_test__/reset", { headers: testHeaders })).ok()).toBeTruthy();
  const response = await request.get(api + "/__presales_test__/context", { headers: testHeaders });
  const context = await response.json() as { token: string };
  await page.addInitScript(token => {
    sessionStorage.setItem("enterprise-doc.upload-token.v1", token);
    localStorage.setItem("enterprise-doc-agent.locale", "zh");
  }, context.token);
});

async function createSheet(page: Page, requirements: string) {
  await page.goto("/#/presales");
  await page.getByLabel("响应表名称").fill("合成资料 · 售前响应浏览器验收");
  await page.getByRole("checkbox", { name: /合成验收-保留策略/ }).check();
  await page.getByRole("checkbox", { name: /合成验收-备份条款/ }).check();
  await page.getByLabel("资料适用范围", { exact: true }).fill("受控浏览器验收，资料与模型输出均为合成测试数据。");
  await page.getByRole("checkbox", { name: "我已确认所选版本适用于本次客户要求" }).check();
  await page.getByRole("textbox", { name: "2. 填写客户要求" }).fill(requirements);
  await page.getByRole("button", { name: "保存响应表", exact: true }).click();
  await expect(page.getByRole("article", { name: "R1", exact: true })).toBeVisible();
}

async function expectNoHorizontalOverflow(page: Page) {
  const excess = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(excess).toBeLessThanOrEqual(1);
}

test("desktop: five assessments, evidence, review history, reload and real CSV", async ({ page, request }, info) => {
  const errors: string[] = [];
  const rejectedRequests: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("response", response => {
    if (response.status() >= 400 && response.url().includes("/api/")) rejectedRequests.push(`${response.status()} ${response.request().method()} ${new URL(response.url()).pathname}`);
  });
  const statuses = ["supported", "conditional", "contradicted", "insufficient_evidence", "conflicting_evidence"];
  await createSheet(page, statuses.map((status, i) => `[${status}] Retention 要求 ${i + 1}\t采购条款 ${i + 1}`).join("\n"));
  await page.getByRole("button", { name: "生成待处理要求" }).click();
  await expect(page.locator(".presales-answer")).toHaveCount(5);
  for (const [index, label] of ["支持", "有条件支持", "不满足", "证据不足", "证据冲突"].entries()) {
    await expect(page.getByRole("article", { name: `R${index + 1}`, exact: true }).locator(".presales-state")).toHaveText(label);
  }
  const statResponse = await request.get(api + "/__presales_test__/stats", { headers: testHeaders });
  const stats = await statResponse.json() as { mockProviderRequests: number };
  expect(stats.mockProviderRequests).toBe(5);
  await expect(page.getByRole("button", { name: "导出已复核 CSV" })).toBeDisabled();
  await expectNoHorizontalOverflow(page);
  await page.screenshot({ path: info.outputPath("desktop-overview.png"), fullPage: true });
  for (let index = 0; index < 5; index += 1) {
    const row = page.getByRole("article", { name: `R${index + 1}`, exact: true });
    await row.getByText("查看证据与复核", { exact: true }).click();
    if (index !== 3) await expect(row.getByRole("blockquote").first()).toContainText("Retention");
    if (index === 1) await expect(row.getByText("需采用指定配置并确认合同范围。", { exact: true }).first()).toBeVisible();
    if (index === 3) await expect(row.getByText("请补充当前有效的证明材料。", { exact: true }).first()).toBeVisible();
    await expect(row.getByText(/部分片段已按长度或数量限制截取/)).toBeVisible();
    await row.getByRole("textbox", { name: "响应文案", exact: true }).fill(index === 0 ? "=客户原文\n人工复核后的中文响应" : `人工复核第 ${index + 1} 条`);
    await row.getByLabel("复核备注", { exact: true }).fill("已核对合成资料，非客户验收。");
    await row.getByRole("button", { name: "保存复核", exact: true }).click();
    await expect(row.locator(".presales-reviewed")).toHaveText("已复核");
  }
  await expect(page.getByRole("button", { name: "导出已复核 CSV" })).toBeEnabled();
  const first = page.getByRole("article", { name: "R1", exact: true });
  await first.getByText("原模型草稿", { exact: true }).click();
  await expect(first.locator(".presales-original").first()).toContainText("受控浏览器验收输出");
  await page.screenshot({ path: info.outputPath("desktop-evidence-review.png"), fullPage: true });
  const [download] = await Promise.all([
    page.waitForEvent("download"), page.getByRole("button", { name: "导出已复核 CSV" }).click(),
  ]);
  expect(download.suggestedFilename()).toBe("presales-responses.csv");
  const file = await download.path();
  expect(file).not.toBeNull();
  const bytes = readFileSync(file);
  expect([...bytes.subarray(0, 3)]).toEqual([0xef, 0xbb, 0xbf]);
  const csv = bytes.toString("utf8");
  expect(csv).toContain("'=客户原文\n人工复核后的中文响应");
  expect(csv).toContain("已复核");
  expect(csv).toContain("Retention is 30 days.");
  expect(csv).toContain("Retention is 90 days.");
  await download.saveAs(info.outputPath("reviewed-responses.csv"));
  await page.reload();
  await expect(page.getByRole("button", { name: "导出已复核 CSV" })).toBeEnabled();
  await expect(page.locator(".presales-reviewed")).toHaveCount(5);
  const storage = await page.evaluate(() => JSON.stringify({ ...sessionStorage, ...localStorage }));
  expect(storage).not.toContain("人工复核后的中文响应");
  expect(storage).not.toContain("Retention is");
  expect(errors).toEqual([]);
  expect(rejectedRequests).toEqual([]);
});

test("mobile: a failed row preserves earlier results and retries only on request", async ({ page, request }, info) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await createSheet(page, "[supported] Retention 第一条\n[supported] Retention timeout-once 第二条");
  await page.getByRole("button", { name: "生成待处理要求" }).click();
  const first = page.getByRole("article", { name: "R1", exact: true });
  const second = page.getByRole("article", { name: "R2", exact: true });
  await expect(first.locator(".presales-answer")).toBeVisible();
  await expect(second.getByRole("alert")).toContainText("本次暂时未能完成生成");
  await expect(second.getByRole("alert")).toContainText("要求和资料已保留");
  let stats = await (await request.get(api + "/__presales_test__/stats", { headers: testHeaders })).json() as { mockProviderRequests: number };
  expect(stats.mockProviderRequests).toBe(2);
  await second.getByRole("button", { name: "重试本条" }).click();
  await expect(second.locator(".presales-answer")).toBeVisible();
  stats = await (await request.get(api + "/__presales_test__/stats", { headers: testHeaders })).json() as { mockProviderRequests: number };
  expect(stats.mockProviderRequests).toBe(3);
  await first.getByText("查看证据与复核", { exact: true }).click();
  await first.getByRole("textbox", { name: "响应文案", exact: true }).fill("窄屏已核查的响应");
  await first.getByRole("button", { name: "保存复核" }).click();
  await expect(first.locator(".presales-reviewed")).toHaveText("已复核");
  await expectNoHorizontalOverflow(page);
  await page.screenshot({ path: info.outputPath("mobile-evidence-review.png"), fullPage: true });
  const [download] = await Promise.all([
    page.waitForEvent("download"), page.getByRole("button", { name: "导出草稿 CSV" }).click(),
  ]);
  const downloaded = await download.path();
  expect(readFileSync(downloaded, "utf8")).toContain("未复核草稿");
  await page.reload();
  await expect(page.locator(".presales-answer")).toHaveCount(2);
});

test("revocation: an open sheet hides evidence and cannot export after a real grant is removed", async ({ page, request }) => {
  await createSheet(page, "[supported] Retention 授权撤回验收");
  await page.getByRole("button", { name: "生成待处理要求" }).click();
  await expect(page.locator(".presales-answer")).toHaveCount(1);
  await page.getByText("查看证据与复核", { exact: true }).click();
  await expect(page.getByRole("blockquote")).toBeVisible();
  expect((await request.post(api + "/__presales_test__/revoke", { headers: testHeaders })).ok()).toBeTruthy();
  await page.getByRole("button", { name: "导出草稿 CSV" }).click();
  await expect(page.getByRole("button", { name: "返回新建响应表" })).toBeVisible();
  await expect(page.getByRole("blockquote")).toHaveCount(0);
  await expect(page.locator(".presales-answer")).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("article")).toHaveCount(0);
});
