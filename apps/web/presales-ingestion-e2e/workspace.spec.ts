import { expect, test, type APIRequestContext, type Locator, type Page } from "@playwright/test";
import { createHash, randomUUID } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";

const api = "http://127.0.0.1:18766";
const testHeaders = { "X-Presales-Test": "presales-ingestion" };
interface Fixture { name: string; mediaType: string; sizeBytes: number; sha256: string; excerpt: string; heading: string | null; pageNumber: number | null }
interface Context { token: string; otherToken: string; tenantId: string; actorId: string; fixtures: Record<string, Fixture> }
interface PacketEvidence {
  sources: { versionId: string; contentSha256: string }[];
  rows: { draft: { citations: { chunkId: string; documentVersionId: string; excerpt: string; heading: string | null; pageNumber: number | null }[] } | null }[];
}
interface Snapshot {
  uploads: { id: string; filename: string; status: string; sha256: string; sizeBytes: number; versionId: string }[];
  versions: { id: string; documentId: string; filename: string; status: string; declaredSha256: string; objectSha256: string; contentSha256Verified: boolean }[];
  generations: { id: string; versionId: string; status: string; stage: string; active: boolean; chunkCount: number; embeddedCount: number; errorCode: string | null }[];
  chunks: { id: string; versionId: string; text: string; heading: string | null; pageNumber: number | null; embeddingPresent: boolean }[];
  jobs: { id: string; versionId: string; status: string; attempts: number; errorCode: string | null }[];
  attempts: { jobId: string; status: string; workerId: string; errorCode: string | null }[];
  outbox: { id: string; jobId: string; status: string; attempts: number }[];
  mockModelRequests: unknown[];
  sentinel: { jobStatus: string; attempts: number; outboxStatus: string; outboxAttempts: number };
}

async function getContext(request: APIRequestContext): Promise<Context> {
  const response = await request.get(api + "/__ingestion_test__/context", { headers: testHeaders });
  if (!response.ok()) throw new Error("Ingestion test context unavailable.");
  return await response.json() as Context;
}
async function snapshot(request: APIRequestContext): Promise<Snapshot> {
  const response = await request.get(api + "/__ingestion_test__/snapshot", { headers: testHeaders });
  expect(response.ok()).toBeTruthy();
  return await response.json() as Snapshot;
}
async function publish(request: APIRequestContext, action: "pause" | "resume") {
  expect((await request.post(api + "/__ingestion_test__/publisher/" + action, { headers: testHeaders })).ok()).toBeTruthy();
}
async function connect(page: Page, context: Context) {
  await page.addInitScript(() => localStorage.setItem("enterprise-doc-agent.locale", "zh"));
  await page.goto("/#/documents");
  await page.getByRole("button", { name: "打开开发访问" }).click();
  await page.getByLabel("本地 API 令牌").fill(context.token);
  await page.getByRole("button", { name: "保存令牌", exact: true }).click();
  await page.getByLabel("本地 API 令牌").fill("");
  await expect(page.locator(".session-identity")).toContainText(context.tenantId.slice(0, 8));
  await page.getByRole("button", { name: "关闭上传" }).click();
}
async function upload(page: Page, request: APIRequestContext, context: Context, key: string) {
  const fixture = context.fixtures[key];
  const file = await request.get(api + "/__ingestion_test__/files/" + key, { headers: testHeaders });
  expect(file.ok()).toBeTruthy();
  const buffer = await file.body();
  expect(createHash("sha256").update(buffer).digest("hex")).toBe(fixture.sha256);
  await page.getByRole("button", { name: "上传文档", exact: true }).first().click();
  const drawer = page.getByRole("dialog", { name: "上传文档" });
  await drawer.getByLabel("本地 API 令牌").fill("");
  const failures: string[] = [];
  const onFailed = (failed: import("@playwright/test").Request) => failures.push(`${failed.method()} ${new URL(failed.url()).origin} ${failed.failure()?.errorText ?? "network failure"}`);
  page.on("requestfailed", onFailed);
  const completed = page.waitForResponse(response => new URL(response.url()).pathname.endsWith("/complete") && response.request().method() === "POST", { timeout: 30_000 }).catch(() => null);
  await drawer.getByLabel("选择文档", { exact: true }).setInputFiles({ name: fixture.name, mimeType: fixture.mediaType, buffer });
  const response = await completed;
  page.off("requestfailed", onFailed);
  if (response === null) throw new Error("Upload did not complete: " + JSON.stringify({ failedParts: await drawer.locator(".part-state.failed").count(), networkFailures: failures }));
  expect(response.ok()).toBeTruthy();
  const value = await response.json() as { versionId: string };
  await expect(drawer.getByRole("heading", { name: "上传完成", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "关闭上传" }).click();
  return value.versionId;
}
function documentRow(page: Page, name: string, mobile = false): Locator {
  return mobile ? page.locator(".mobile-document-card").filter({ hasText: name }) : page.getByRole("row").filter({ hasText: name });
}
async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(1);
}
function assertSentinel(value: Snapshot) {
  expect(value.sentinel).toMatchObject({ jobStatus: "pending", attempts: 0, outboxStatus: "pending", outboxAttempts: 0 });
}
function assertReady(value: Snapshot, fixture: Fixture, versionId: string) {
  expect(value.uploads.find(item => item.versionId === versionId)).toMatchObject({ status: "completed", filename: fixture.name, sha256: fixture.sha256, sizeBytes: fixture.sizeBytes });
  expect(value.versions.find(item => item.id === versionId)).toMatchObject({ status: "ready", declaredSha256: fixture.sha256, objectSha256: fixture.sha256, contentSha256Verified: true });
  expect(value.generations.find(item => item.versionId === versionId)).toMatchObject({ status: "succeeded", stage: "ready", active: true, chunkCount: 1, embeddedCount: 1, errorCode: null });
  expect(value.chunks.filter(item => item.versionId === versionId)).toEqual([expect.objectContaining({ text: fixture.excerpt, heading: fixture.heading, pageNumber: fixture.pageNumber, embeddingPresent: true })]);
  const job = value.jobs.find(item => item.versionId === versionId);
  expect(job).toMatchObject({ status: "succeeded", attempts: 1, errorCode: null });
  expect(value.attempts.find(item => item.jobId === job?.id)).toMatchObject({ status: "succeeded", workerId: expect.stringMatching(/^presales-ingestion-/) });
  expect(value.outbox.find(item => item.jobId === job?.id)).toMatchObject({ status: "published", attempts: 1 });
}

// Playwright error snapshots can include password values even without tracing.
test.afterEach(async ({ page }) => {
  if (!page.isClosed()) await page.locator('input[type="password"]').evaluateAll(inputs => {
    for (const input of inputs) { (input as HTMLInputElement).value = ""; input.removeAttribute("value"); }
  });
});

test("desktop: real TXT PDF DOCX ingestion, evidence, review, recovery and tenant boundary", async ({ page, request }, info) => {
  const context = await getContext(request);
  await publish(request, "pause");
  const errors: string[] = [];
  const workers: string[] = [];
  const uploads: { filename: string; sha256: string }[] = [];
  let successfulObjectPuts = 0;
  page.on("pageerror", error => errors.push(error.message));
  page.on("worker", worker => workers.push(new URL(worker.url()).pathname));
  page.on("response", response => {
    const url = new URL(response.url());
    if (url.port === "9000" && response.request().method() === "PUT" && response.ok()) successfulObjectPuts += 1;
    if (url.pathname === "/api/upload-sessions" && response.request().method() === "POST") {
      const body = response.request().postDataJSON() as { filename: string; sha256: string };
      uploads.push({ filename: body.filename, sha256: body.sha256 });
    }
  });
  await connect(page, context);
  const ids: Record<string, string> = {};
  for (const key of ["txt", "pdf", "docx"]) ids[key] = await upload(page, request, context, key);
  for (const key of ["txt", "pdf", "docx"]) {
    const row = documentRow(page, context.fixtures[key].name);
    await expect(row.locator(".status-badge")).toHaveText("处理中");
    await expect(row.getByRole("button", { name: "创建响应表" })).toBeDisabled();
  }
  const pending = await snapshot(request);
  expect(pending.versions).toHaveLength(3);
  expect(pending.generations).toHaveLength(0);
  expect(pending.jobs.every(job => job.status === "pending" && job.attempts === 0)).toBe(true);
  expect(pending.outbox.every(event => event.status === "pending" && event.attempts === 0)).toBe(true);
  assertSentinel(pending);
  const denied = await request.post(api + "/api/presales", {
    headers: { Authorization: "Bearer " + context.token, "Idempotency-Key": randomUUID() },
    data: { title: "Pending source rejection", sources: [{ versionId: ids.txt, applicability: "Synthetic acceptance" }], requirements: [{ key: "R1", text: "Retention", sourceLocation: "" }] },
  });
  expect(denied.status()).toBe(404);
  expect(await denied.json()).toMatchObject({ error: { code: "presales_source_unavailable" } });
  await page.screenshot({ path: info.outputPath("desktop-processing.png"), fullPage: true });

  await publish(request, "resume");
  for (const key of ["txt", "pdf", "docx"]) await expect(documentRow(page, context.fixtures[key].name).getByRole("button", { name: "创建响应表" })).toBeEnabled();
  const ready = await snapshot(request);
  for (const key of ["txt", "pdf", "docx"]) assertReady(ready, context.fixtures[key], ids[key]);
  assertSentinel(ready);
  expect(successfulObjectPuts).toBe(3);
  expect(workers.some(path => path.includes("hash.worker"))).toBe(true);
  expect(uploads).toEqual(["txt", "pdf", "docx"].map(key => ({ filename: context.fixtures[key].name, sha256: context.fixtures[key].sha256 })));
  await noOverflow(page);
  await page.screenshot({ path: info.outputPath("desktop-ready.png"), fullPage: true });

  await documentRow(page, context.fixtures.txt.name).getByRole("button", { name: "创建响应表" }).click();
  await expect(page.getByRole("checkbox", { name: new RegExp(context.fixtures.txt.name) })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: "我已确认所选版本适用于本次客户要求" })).not.toBeChecked();
  await page.getByLabel("响应表名称", { exact: true }).fill("真实上传链路 · 合成资料响应验收");
  for (const key of ["pdf", "docx"]) await page.getByRole("checkbox", { name: new RegExp(context.fixtures[key].name) }).check();
  await page.getByLabel("资料适用范围", { exact: true }).fill("仅合成文件和受控模型的本地链路验收，未经过客户验证。");
  await page.getByRole("checkbox", { name: "我已确认所选版本适用于本次客户要求" }).check();
  await page.getByRole("textbox", { name: "2. 填写客户要求" }).fill("Retention 数据保留期限\t要求 1\nExport 导出格式\t要求 2\nBackups 备份保留期限\t要求 3");
  await page.getByRole("button", { name: "保存响应表", exact: true }).click();
  await expect(page.getByRole("article", { name: "R1", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "生成待处理要求" }).click();
  await expect(page.locator(".presales-answer")).toHaveCount(3);
  await expect(page.getByRole("button", { name: "导出已复核 CSV" })).toBeDisabled();
  for (const [index, key] of ["txt", "pdf", "docx"].entries()) {
    const row = page.getByRole("article", { name: "R" + (index + 1), exact: true });
    await row.getByText("查看证据与复核", { exact: true }).click();
    await expect(row.getByRole("blockquote")).toHaveText(context.fixtures[key].excerpt);
    if (context.fixtures[key].heading) await expect(row.locator(".presales-evidence")).toContainText(context.fixtures[key].heading);
    await row.getByRole("textbox", { name: "响应文案", exact: true }).fill(index === 0 ? "=核对原文\n已核对上传资料中的保留期限。" : "已核对上传资料第 " + (index + 1) + " 条。");
    await row.getByRole("textbox", { name: "复核备注", exact: true }).fill("合成资料技术验收，非客户或专家审定。");
    await row.getByRole("button", { name: "保存复核", exact: true }).click();
    await expect(row.locator(".presales-reviewed")).toHaveText("已复核");
  }
  const packetId = await page.evaluate(key => sessionStorage.getItem(key), `enterprise.presales.active:${context.tenantId}:${context.actorId}`);
  expect(packetId).toMatch(/^[0-9a-f-]{36}$/);
  const packetResponse = await request.get(api + "/api/presales/" + packetId, { headers: { Authorization: "Bearer " + context.token } });
  expect(packetResponse.ok()).toBeTruthy();
  const packet = await packetResponse.json() as PacketEvidence;
  for (const key of ["txt", "pdf", "docx"]) {
    expect(packet.sources.find(source => source.versionId === ids[key])?.contentSha256).toBe(context.fixtures[key].sha256);
    const citation = packet.rows.flatMap(row => row.draft?.citations ?? []).find(c => c.documentVersionId === ids[key]);
    expect(citation).toMatchObject({ excerpt: context.fixtures[key].excerpt, heading: context.fixtures[key].heading, pageNumber: context.fixtures[key].pageNumber });
    expect(ready.chunks.find(chunk => chunk.id === citation?.chunkId)?.text).toContain(citation?.excerpt);
  }
  const foreignSource = await request.post(api + "/api/presales", {
    headers: { Authorization: "Bearer " + context.otherToken, "Idempotency-Key": randomUUID() },
    data: { title: "Cross tenant rejection", sources: [{ versionId: ids.txt, applicability: "Synthetic acceptance" }], requirements: [{ key: "R1", text: "Retention", sourceLocation: "" }] },
  });
  const foreignPacket = await request.get(api + "/api/presales/" + packetId, { headers: { Authorization: "Bearer " + context.otherToken } });
  expect(foreignSource.status()).toBe(404); expect(foreignPacket.status()).toBe(404);
  expect(await foreignSource.json()).toMatchObject({ error: { code: "presales_source_unavailable" } });
  expect(await foreignPacket.json()).toMatchObject({ error: { code: "presales_not_found" } });
  await noOverflow(page);
  await page.screenshot({ path: info.outputPath("desktop-reviewed.png"), fullPage: true });
  await page.getByRole("article", { name: "R1", exact: true }).screenshot({ path: info.outputPath("desktop-evidence-detail.png") });
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "导出已复核 CSV" }).click()]);
  const downloaded = await download.path();
  const bytes = readFileSync(downloaded);
  expect([...bytes.subarray(0, 3)]).toEqual([0xef, 0xbb, 0xbf]);
  const csv = bytes.toString("utf8");
  expect(csv).toContain("'=核对原文\n已核对上传资料中的保留期限。");
  for (const key of ["txt", "pdf", "docx"]) { expect(csv).toContain(context.fixtures[key].sha256); expect(csv).toContain(context.fixtures[key].excerpt); }
  await download.saveAs(info.outputPath("reviewed-responses.csv"));
  await page.reload();
  await expect(page.locator(".presales-reviewed")).toHaveCount(3);
  await expect(page.getByRole("button", { name: "导出已复核 CSV" })).toBeEnabled();
  const final = await snapshot(request);
  expect(final.mockModelRequests).toHaveLength(3); assertSentinel(final); expect(errors).toEqual([]);
  writeFileSync(info.outputPath("pipeline-evidence.json"), JSON.stringify({ pipeline: final, packet, browser: { workers, uploads, successfulObjectPuts, pageErrors: errors }, rejections: { pendingSource: denied.status(), foreignSource: foreignSource.status(), foreignPacket: foreignPacket.status() } }, null, 2));
});

test("mobile: a parser failure is excluded and a corrected upload reaches a new response sheet", async ({ page, request }, info) => {
  const context = await getContext(request);
  await page.setViewportSize({ width: 390, height: 844 });
  await connect(page, context);
  await publish(request, "resume");
  const brokenId = await upload(page, request, context, "broken_pdf");
  const broken = documentRow(page, context.fixtures.broken_pdf.name, true);
  await expect(broken.locator(".status-badge")).toHaveText("失败");
  await expect(broken).toContainText("pdf_parse_failed");
  await expect(broken.getByRole("button", { name: "创建响应表" })).toBeDisabled();
  await noOverflow(page);
  await page.screenshot({ path: info.outputPath("mobile-parse-failure.png"), fullPage: true });
  const repairedId = await upload(page, request, context, "repaired_pdf");
  const repaired = documentRow(page, context.fixtures.repaired_pdf.name, true);
  await expect(repaired.getByRole("button", { name: "创建响应表" })).toBeEnabled();
  await repaired.getByRole("button", { name: "创建响应表" }).click();
  await expect(page.getByRole("checkbox", { name: new RegExp(context.fixtures.repaired_pdf.name) })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: new RegExp(context.fixtures.broken_pdf.name) })).toHaveCount(0);
  await expect(page.getByRole("checkbox", { name: "我已确认所选版本适用于本次客户要求" })).not.toBeChecked();
  await page.getByLabel("响应表名称", { exact: true }).fill("修复文件 · 窄屏验收");
  await page.getByLabel("资料适用范围", { exact: true }).fill("合成修复文件，受控模型，非客户验收。");
  await page.getByRole("checkbox", { name: "我已确认所选版本适用于本次客户要求" }).check();
  await page.getByRole("textbox", { name: "2. 填写客户要求" }).fill("Export 导出格式核验");
  await page.getByRole("button", { name: "保存响应表", exact: true }).click();
  await page.getByRole("button", { name: "生成待处理要求" }).click();
  await expect(page.locator(".presales-answer")).toHaveCount(1);
  await page.getByText("查看证据与复核", { exact: true }).click();
  await expect(page.getByRole("blockquote")).toHaveText(context.fixtures.repaired_pdf.excerpt);
  await noOverflow(page);
  await page.screenshot({ path: info.outputPath("mobile-recovered-response.png"), fullPage: true });
  const final = await snapshot(request);
  assertReady(final, context.fixtures.repaired_pdf, repairedId);
  expect(final.versions.find(item => item.id === brokenId)?.status).toBe("failed");
  expect(final.generations.find(item => item.versionId === brokenId)).toMatchObject({ status: "failed", active: false, errorCode: "pdf_parse_failed" });
  expect(final.jobs.find(item => item.versionId === brokenId)).toMatchObject({ status: "dead", attempts: 1, errorCode: "pdf_parse_failed" });
  expect(final.chunks.filter(item => item.versionId === brokenId)).toHaveLength(0);
  assertSentinel(final);
  writeFileSync(info.outputPath("recovery-evidence.json"), JSON.stringify({ pipeline: final, brokenId, repairedId }, null, 2));
});
