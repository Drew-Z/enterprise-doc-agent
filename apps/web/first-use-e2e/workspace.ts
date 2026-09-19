import { expect, type APIRequestContext, type Page, type TestInfo } from "@playwright/test";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { z } from "zod";
import { packetSchema, type Packet } from "../src/presales/api";
import { control, privacyAndLayout, session, type Fixture, type FixtureKey } from "./support";

export interface Source { file: Fixture; versionId: string }

export function documentRow(page: Page, filename: string) {
  return (page.viewportSize()?.width ?? 1440) < 768
    ? page.locator(".mobile-document-card").filter({ hasText: filename })
    : page.getByRole("row").filter({ hasText: filename });
}

export async function upload(page: Page, request: APIRequestContext, key: FixtureKey, fixture: Fixture) {
  const buffer = await (await control(request, "/test/files/" + key)).body();
  expect(buffer.length).toBe(fixture.sizeBytes);
  expect(createHash("sha256").update(buffer).digest("hex")).toBe(fixture.sha256);
  await page.getByRole("button", { name: "上传文档", exact: true }).first().click();
  const drawer = page.getByRole("dialog", { name: "上传文档" });
  await expect(drawer.getByLabel("本地 API 令牌")).toHaveCount(0);
  const hashWorker = page.waitForEvent("worker", { predicate: worker => new URL(worker.url()).pathname.includes("hash.worker") });
  const completed = page.waitForResponse(response => new URL(response.url()).pathname.endsWith("/complete") && response.request().method() === "POST");
  await drawer.getByLabel("选择文档", { exact: true }).setInputFiles({ name: fixture.name, mimeType: fixture.mediaType, buffer });
  await hashWorker;
  const response = await completed;
  expect(response.ok()).toBe(true);
  const { versionId } = z.object({ versionId: z.string().uuid() }).parse(await response.json());
  await expect(drawer.getByRole("heading", { name: "上传完成", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "关闭上传", exact: true }).click();
  return versionId;
}

export async function createSheet(page: Page, sources: Source[], title: string, requirements: string) {
  await page.goto("/#/documents");
  await documentRow(page, sources[0].file.name).getByRole("button", { name: "创建响应表" }).click();
  await expect(page.getByRole("checkbox", { name: new RegExp(sources[0].file.name) })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: "我已确认所选版本适用于本次客户要求" })).not.toBeChecked();
  for (const source of sources.slice(1)) await page.getByRole("checkbox", { name: new RegExp(source.file.name) }).check();
  await page.getByLabel("响应表名称", { exact: true }).fill(title);
  await page.getByLabel("资料适用范围", { exact: true }).fill("合成资料和本地受控模型的技术验收，未经客户验证。");
  await page.getByRole("checkbox", { name: "我已确认所选版本适用于本次客户要求" }).check();
  await page.getByRole("textbox", { name: "2. 填写客户要求" }).fill(requirements);
  const created = page.waitForResponse(response => new URL(response.url()).pathname === "/api/presales" && response.request().method() === "POST");
  await page.getByRole("button", { name: "保存响应表", exact: true }).click();
  const response = await created;
  expect(response.ok()).toBe(true);
  const packet = packetSchema.parse(await response.json());
  await expect(page.getByRole("article", { name: "R1", exact: true })).toBeVisible();
  return packet;
}

export async function generate(page: Page, rowCount: number) {
  await page.getByRole("button", { name: "生成待处理要求", exact: true }).click();
  await expect(page.locator(".presales-answer")).toHaveCount(rowCount);
  await expect(page.getByRole("button", { name: "导出已复核 CSV" })).toBeDisabled();
}

export async function reviewAndExport(page: Page, sources: Source[], packet: Packet, info: TestInfo, label: string) {
  let reviewed = packet;
  const actorId = (await session(page)).currentTenant?.actorId;
  for (const [index, source] of sources.entries()) {
    const row = page.getByRole("article", { name: "R" + (index + 1), exact: true });
    await row.getByText("查看证据与复核", { exact: true }).click();
    await expect(row.getByRole("blockquote")).toHaveText(source.file.excerpt);
    if (source.file.heading) await expect(row.locator(".presales-evidence")).toContainText(source.file.heading);
    await row.getByRole("textbox", { name: "响应文案", exact: true }).fill(index === 0 ? "=核对原文\n已核对上传资料。" : "已核对上传资料第 " + (index + 1) + " 条。");
    await row.getByRole("textbox", { name: "复核备注", exact: true }).fill("合成资料技术验收，非客户或专家审定。");
    const saved = page.waitForResponse(response => new URL(response.url()).pathname.endsWith("/review") && response.request().method() === "PUT");
    await row.getByRole("button", { name: "保存复核", exact: true }).click();
    const response = await saved;
    expect(response.ok()).toBe(true);
    reviewed = packetSchema.parse(await response.json());
    await expect(row.locator(".presales-reviewed")).toHaveText("已复核");
  }
  expect(reviewed.id).toBe(packet.id);
  for (const [index, source] of sources.entries()) {
    expect(reviewed.sources.find(item => item.versionId === source.versionId)?.contentSha256).toBe(source.file.sha256);
    expect(reviewed.rows[index].review?.actorId).toBe(actorId);
    expect(reviewed.rows[index].draft?.citations).toEqual([expect.objectContaining({ documentVersionId: source.versionId, excerpt: source.file.excerpt, heading: source.file.heading, pageNumber: source.file.pageNumber })]);
  }
  await privacyAndLayout(page);
  await page.screenshot({ path: info.outputPath(label + "-reviewed.png"), fullPage: true });
  await page.getByRole("article", { name: "R1", exact: true }).screenshot({ path: info.outputPath(label + "-evidence.png") });
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "导出已复核 CSV", exact: true }).click()]);
  await download.saveAs(info.outputPath(label + "-reviewed.csv"));
  const bytes = await readFile(info.outputPath(label + "-reviewed.csv"));
  expect([...bytes.subarray(0, 3)]).toEqual([0xef, 0xbb, 0xbf]);
  const csv = bytes.toString("utf8");
  expect(csv).toContain("'=核对原文\n已核对上传资料。");
  for (const source of sources) {
    expect(csv).toContain(source.file.sha256);
    expect(csv).toContain(source.file.excerpt);
  }
  const restored = page.waitForResponse(response => new URL(response.url()).pathname === "/api/presales/" + packet.id && response.request().method() === "GET");
  await page.reload();
  expect(packetSchema.parse(await (await restored).json())).toEqual(reviewed);
  await expect(page.locator(".presales-reviewed")).toHaveCount(sources.length);
  await expect(page.getByRole("button", { name: "导出已复核 CSV" })).toBeEnabled();
  return { packet: reviewed, csvSha256: createHash("sha256").update(bytes).digest("hex"), bytes: bytes.length };
}

export function sourceAttempt(versionId: string) {
  return { title: "Synthetic access boundary", sources: [{ versionId, applicability: "Synthetic acceptance" }], requirements: [{ key: "R1", text: "Retention", sourceLocation: "" }] };
}
