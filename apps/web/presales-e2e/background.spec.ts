import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";

test.skip(process.env.PRESALES_BACKGROUND_E2E !== "true", "Separate background worker fixture");
const api = "http://127.0.0.1:18765";
const headers = { "X-Presales-Test": "presales-browser" };

test("background: offline completion, read recovery, lost admission, review and one-time settlement", async ({ page, request }, info) => {
  const context = await (await request.get(api + "/__presales_test__/context", { headers })).json() as { token: string; otherToken: string };
  await page.addInitScript(token => {
    sessionStorage.setItem("enterprise-doc.upload-token.v1", token);
    localStorage.setItem("enterprise-doc-agent.locale", "zh");
  }, context.token);
  const posted: string[] = [];
  page.on("request", req => { if (req.method() === "POST" && req.url().endsWith("/generate")) posted.push(req.url()); });
  await page.goto("/#/presales");
  await page.getByLabel("响应表名称").fill("后台生成故障恢复验收");
  await page.getByRole("checkbox", { name: /contract-/ }).check();
  await page.getByLabel("资料适用范围", { exact: true }).fill("合成资料验收");
  await page.getByRole("checkbox", { name: "我已确认所选版本适用于本次客户要求" }).check();
  await page.getByRole("textbox", { name: "2. 填写客户要求" }).fill("Retention transient\nRetention normal\nRetention terminal");
  const [created] = await Promise.all([
    page.waitForResponse(response => new URL(response.url()).pathname === "/api/presales" && response.request().method() === "POST"),
    page.getByRole("button", { name: "保存响应表", exact: true }).click(),
  ]);
  expect(created.status()).toBe(201);
  const sheet = await created.json() as { id: string };
  await page.getByRole("button", { name: "生成待处理要求" }).click();
  await expect(page.getByText(/已受理。任务将在后台继续/)).toBeVisible();
  await expect(page.getByRole("button", { name: "新建响应表", exact: true })).toBeEnabled();
  await expect(page.getByRole("article", { name: "R2", exact: true }).locator(".presales-state")).toHaveText("排队中");
  await page.getByRole("button", { name: "上传与管理资料", exact: true }).click();
  await page.goto("/#/presales");
  await page.reload();
  await expect(page.getByRole("article", { name: "R2", exact: true }).locator(".presales-state")).toHaveText("排队中");
  expect(posted).toHaveLength(1);

  // Only the browser goes offline. The independent request context can observe
  // the real worker and database while the page has no connection.
  await page.context().setOffline(true);
  expect(await page.evaluate(() => navigator.onLine)).toBe(false);
  expect((await request.post(api + "/__presales_test__/release", { headers })).ok()).toBeTruthy();
  await expect.poll(async (): Promise<unknown> => (await request.get(api + "/__presales_test__/stats", { headers })).json()).toMatchObject({ calls: 4, consumed: 2, released: 1 });
  await expect(page.locator(".presales-answer")).toHaveCount(0);
  expect(posted).toHaveLength(1);
  await page.screenshot({ path: info.outputPath("background-offline.png"), fullPage: true });
  await page.context().setOffline(false);
  await expect(page.locator(".presales-answer")).toHaveCount(2);
  await expect(page.getByRole("article", { name: "R3", exact: true }).getByRole("alert")).toBeVisible();
  await expect(page.getByText("2 / 3 条已生成 · 0 / 3 条已复核")).toBeVisible();
  const before = await (await request.get(api + "/__presales_test__/stats", { headers })).json() as { calls: number; consumed: number; released: number };
  expect(before).toMatchObject({ calls: 4, consumed: 2, released: 1 });
  await page.screenshot({ path: info.outputPath("background-partial.png"), fullPage: true });

  // A failed GET hides the cached sheet, but can recover it without a new sheet
  // or generation request. Exercise the recovery controls at mobile width too.
  const sheetRoute = "**/api/presales/" + sheet.id;
  await page.route(sheetRoute, route => route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: { code: "temporarily_unavailable", message: "Temporary read failure", requestId: "browser-read-retry" } }) }));
  await page.locator(".presales-packet-heading").getByRole("button", { name: "刷新", exact: true }).click();
  await expect(page.getByRole("article")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "重试读取", exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
  await page.screenshot({ path: info.outputPath("background-read-retry.png"), fullPage: true });
  await page.unroute(sheetRoute);
  await page.getByRole("button", { name: "重试读取", exact: true }).click();
  await expect(page.locator(".presales-answer")).toHaveCount(2);
  await expect(page.locator(".presales-main > .presales-error")).toHaveCount(0);
  expect(posted).toHaveLength(1);

  expect((await request.post(api + "/__presales_test__/repair", { headers })).ok()).toBeTruthy();
  const retryRoute = sheetRoute + "/rows/*/generate";
  let lostAdmissions = 0;
  await page.route(retryRoute, async route => {
    const response = await route.fetch();
    expect(response.status()).toBe(202);
    // The actual API has admitted the request. Drop that response and the
    // immediate recovery GET, without cancelling the durable server job.
    await page.context().setOffline(true);
    await route.abort("connectionreset");
    lostAdmissions += 1;
  });
  await page.getByRole("button", { name: "重试失败条目", exact: true }).click();
  await expect.poll(() => lostAdmissions).toBe(1);
  expect(await page.evaluate(() => navigator.onLine)).toBe(false);
  await expect(page.locator(".presales-main > .presales-error")).toBeVisible();
  await expect.poll(async (): Promise<unknown> => (await request.get(api + "/__presales_test__/stats", { headers })).json()).toMatchObject({ calls: 5, consumed: 3, released: 1 });
  await page.screenshot({ path: info.outputPath("background-lost-admission.png"), fullPage: true });
  await page.unroute(retryRoute);
  await page.context().setOffline(false);
  await expect(page.locator(".presales-answer")).toHaveCount(3);
  await expect(page.locator(".presales-main > .presales-error")).toHaveCount(0);
  expect(posted).toHaveLength(2);
  const after: unknown = await (await request.get(api + "/__presales_test__/stats", { headers })).json();
  expect(after).toMatchObject({ calls: 5, consumed: 3, released: 1 });

  for (let index = 1; index <= 3; index += 1) {
    const row = page.getByRole("article", { name: `R${index}`, exact: true });
    await row.getByText("查看证据与复核", { exact: true }).click();
    await expect(row.getByRole("blockquote").first()).toContainText("Retention is 30 days.");
    await row.getByRole("textbox", { name: "响应文案", exact: true }).fill(`浏览器恢复后复核第 ${index} 条`);
    await row.getByLabel("复核备注", { exact: true }).fill("已核查受控合成资料，非客户验收。");
    await row.getByRole("button", { name: "保存复核", exact: true }).click();
    await expect(row.locator(".presales-reviewed")).toHaveText("已复核");
  }
  await page.reload();
  await expect(page.locator(".presales-reviewed")).toHaveCount(3);
  const [download] = await Promise.all([
    page.waitForEvent("download"), page.getByRole("button", { name: "导出已复核 CSV" }).click(),
  ]);
  const csv = readFileSync(await download.path(), "utf8");
  for (let index = 1; index <= 3; index += 1) expect(csv).toContain(`浏览器恢复后复核第 ${index} 条`);
  expect(csv).toContain("Retention is 30 days.");
  await download.saveAs(info.outputPath("background-reviewed.csv"));
  expect(posted).toHaveLength(2);
  expect(await (await request.get(api + "/__presales_test__/stats", { headers })).json()).toEqual(after);
  const storage = await page.evaluate(() => JSON.stringify({ ...sessionStorage, ...localStorage }));
  expect(storage).not.toContain("浏览器恢复后复核");
  expect(storage).not.toContain("Retention is 30 days.");
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
  await page.screenshot({ path: info.outputPath("background-mobile.png"), fullPage: true });
  await info.attach("recovery-accounting", { body: JSON.stringify({ generationPosts: posted.length, lostAdmissions, before, after }), contentType: "application/json" });
  const denied = await request.get(api + "/api/presales/" + sheet.id, { headers: { Authorization: "Bearer " + context.otherToken } });
  expect([403, 404]).toContain(denied.status());
  await page.addInitScript(token => sessionStorage.setItem("enterprise-doc.upload-token.v1", token), context.otherToken);
  await page.reload();
  await expect(page.getByRole("article")).toHaveCount(0);
});
