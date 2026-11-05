import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import path from "node:path";

const api = "http://127.0.0.1:18765";
const headers = { "X-Workbook-Test": "workbook-browser" };
const root = path.resolve(import.meta.dirname, "../../..");
const python = path.join(root, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");

for (const width of [1440, 390]) {
  test(`Excel import, review, reload and original delivery at ${width}px`, async ({ page, request }, info) => {
    const context = await (await request.get(api + "/__workbook_test__/context", { headers })).json() as { token: string; versionId: string };
    await page.addInitScript(token => { sessionStorage.setItem("enterprise-doc.upload-token.v1", token); localStorage.setItem("enterprise-doc-agent.locale", "zh"); }, context.token);
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/#/presales");
    await page.getByLabel("响应表名称").fill(`Excel 客户问卷 ${width}`);
    await page.locator(".presales-source-option").first().getByRole("checkbox").check();
    await page.getByLabel("资料适用范围", { exact: true }).fill("受控测试资料");
    await page.getByRole("checkbox", { name: "我已确认所选版本适用于本次客户要求" }).check();
    await page.getByLabel("客户要求录入方式").selectOption("excel");
    const fixture = await request.get(api + "/__workbook_test__/fixture", { headers });
    await page.getByLabel("客户 Excel 文件").setInputFiles({ name: "客户问卷.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: await fixture.body() });
    await expect(page.getByLabel("工作表", { exact: true })).toHaveValue("技术要求");
    await page.getByRole("button", { name: "预览所选单元格" }).click();
    await expect(page.getByText("将导入 2 条问题")).toBeVisible();
    await expect(page.getByRole("button", { name: "保存响应表", exact: true })).toBeDisabled();
    await page.screenshot({ path: info.outputPath(`mapping-${width}.png`), fullPage: true });
    await page.getByRole("checkbox", { name: /我已核对以下问题/ }).check();
    await page.getByRole("button", { name: "保存响应表", exact: true }).click();
    await expect(page.getByRole("article", { name: "X2", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "回填已复核 Excel" })).toBeDisabled();
    await page.getByRole("button", { name: "生成待处理要求" }).click();
    for (const key of ["X2", "X3"]) {
      const row = page.getByRole("article", { name: key, exact: true });
      await expect(row.locator(".presales-answer")).toBeVisible();
      await row.getByText("查看证据与复核", { exact: true }).click();
      await row.getByRole("textbox", { name: "响应文案", exact: true }).fill(`人工复核 ${key}`);
      await row.getByRole("button", { name: "保存复核", exact: true }).click();
      await expect(row.locator(".presales-reviewed")).toHaveText("已复核");
    }
    await page.reload();
    await expect(page.getByText(/原始问卷: 客户问卷.xlsx/)).toBeVisible();
    const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "回填已复核 Excel" }).click()]);
    expect(download.suggestedFilename()).toBe("presales-reviewed.xlsx");
    const file = info.outputPath(`reviewed-${width}.xlsx`);
    await download.saveAs(file);
    const verification = execFileSync(python, ["-B", "-X", "utf8", "-c", "import sys; from openpyxl import load_workbook; w=load_workbook(sys.argv[1]); assert '人工复核 X2' in w['技术要求']['C2'].value; assert '人工复核 X3' in w['技术要求']['C3'].value; assert w['技术要求']['D2'].value == '=A2'; assert w['采购说明']['A1'].value == '请保留本页。'; print('verified')", file], { encoding: "utf8" });
    expect(verification.trim()).toBe("verified");
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(1);
    await page.screenshot({ path: info.outputPath(`reviewed-${width}.png`), fullPage: true });
    expect(await page.evaluate(() => JSON.stringify({ ...sessionStorage, ...localStorage }))).not.toContain("contentBase64");
  });
}

for (const width of [1440, 390]) {
  test(`Human completion, corrected citations and reviewed delivery at ${width}px`, async ({ page, request }, info) => {
    const context = await (await request.get(api + "/__workbook_test__/context", { headers })).json() as { token: string };
    const before = await (await request.get(api + "/__workbook_test__/stats", { headers })).json() as { modelCalls: number };
    await page.addInitScript(token => { sessionStorage.setItem("enterprise-doc.upload-token.v1", token); localStorage.setItem("enterprise-doc-agent.locale", "zh"); }, context.token);
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/#/presales");
    await page.getByLabel("响应表名称").fill(`人工问卷 ${width}`);
    await page.locator(".presales-source-option").first().getByRole("checkbox").check();
    await page.getByLabel("资料适用范围", { exact: true }).fill("人工核验测试");
    await page.getByRole("checkbox", { name: "我已确认所选版本适用于本次客户要求" }).check();
    await page.getByLabel("客户要求录入方式").selectOption("excel");
    const fixture = await request.get(api + "/__workbook_test__/fixture", { headers });
    await page.getByLabel("客户 Excel 文件").setInputFiles({ name: "人工问卷.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: await fixture.body() });
    await expect(page.getByLabel("工作表", { exact: true })).toHaveValue("技术要求");
    await page.getByRole("button", { name: "预览所选单元格" }).click();
    await page.getByRole("checkbox", { name: /我已核对以下问题/ }).check();
    await page.getByRole("button", { name: "保存响应表", exact: true }).click();
    let writes = 0;
    await page.route("**/manual-response", async route => {
      writes++;
      const response = await route.fetch();
      expect(response.status()).toBe(200);
      if (width === 1440 && writes === 1) await route.abort("failed");
      else await route.fulfill({ response });
    });
    let reviews = 0;
    await page.route("**/review", async route => {
      reviews++;
      const response = await route.fetch();
      expect(response.status()).toBe(200);
      if (width === 1440 && reviews === 1) await route.abort("failed");
      else await route.fulfill({ response });
    });
    for (const key of ["X2", "X3"]) {
      const row = page.getByRole("article", { name: key, exact: true });
      await row.getByRole("button", { name: "人工填写", exact: true }).click();
      await row.getByRole("button", { name: "查询原文", exact: true }).click();
      await row.getByRole("button", { name: "选择证据 1", exact: true }).click();
      await row.getByRole("button", { name: "确认所选证据并填写" }).click();
      await row.getByLabel("判断", { exact: true }).selectOption("supported");
      await row.getByRole("textbox", { name: "响应文案", exact: true }).fill(`人工填写 ${key}`);
      await row.getByLabel("填写依据", { exact: true }).fill("人工核对原文及适用范围");
      await row.getByRole("button", { name: "保存人工草稿" }).click();
      await expect(row.getByText(/初稿由人工填写/)).toBeVisible();
      await expect(page.getByRole("button", { name: "回填已复核 Excel" })).toBeDisabled();
      await row.getByRole("button", { name: `${key} 查看证据与复核` }).click();
      await row.getByRole("textbox", { name: "响应文案", exact: true }).fill(`人工更正 ${key}`);
      await row.getByRole("button", { name: "更正引用证据" }).click();
      await row.getByLabel("原文关键词").fill("days.");
      await row.getByRole("button", { name: "查询原文", exact: true }).click();
      await row.getByRole("button", { name: "选择证据 1", exact: true }).click();
      await row.getByRole("button", { name: "移除证据 1", exact: true }).click();
      await expect(row.getByRole("textbox", { name: "响应文案", exact: true })).toHaveValue(`人工更正 ${key}`);
      await row.getByLabel("复核备注", { exact: true }).fill("独立复核测试，不代表客户验收");
      await row.getByRole("button", { name: "保存复核", exact: true }).click();
      await expect(row.locator(".presales-reviewed")).toHaveText("已复核");
    }
    expect(writes).toBe(2);
    expect(reviews).toBe(2);
    await page.reload();
    await expect(page.getByText(/初稿由人工填写/)).toHaveCount(2);
    const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "回填已复核 Excel" }).click()]);
    const file = info.outputPath(`manual-reviewed-${width}.xlsx`);
    await download.saveAs(file);
    execFileSync(python, ["-B", "-X", "utf8", "-c", "import sys; from openpyxl import load_workbook; w=load_workbook(sys.argv[1]); assert '人工更正 X2' in w['技术要求']['C2'].value; assert '人工更正 X3' in w['技术要求']['C3'].value; assert w['技术要求']['D2'].value == '=A2'; assert w['采购说明']['A1'].value == '请保留本页。'", file]);
    const [audit] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "导出已复核 CSV", exact: true }).click()]);
    const auditFile = info.outputPath(`citations-audit-${width}.csv`);
    await audit.saveAs(auditFile);
    execFileSync(python, ["-B", "-X", "utf8", "-c", "import sys,csv; rows=list(csv.DictReader(open(sys.argv[1],encoding='utf-8-sig'))); assert len(rows)==2; assert all(r['原文证据'].endswith(': days.') and 'Retention' in r['原始引用证据'] and '版本 2' in r['人工引用修订记录'] and '人工填写' in r['原人工文案'] for r in rows)", auditFile]);
    const after = await (await request.get(api + "/__workbook_test__/stats", { headers })).json() as { modelCalls: number };
    expect(after.modelCalls).toBe(before.modelCalls);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(1);
    expect(await page.evaluate(() => JSON.stringify({ ...sessionStorage, ...localStorage }))).not.toContain("人工填写 X2");
    const first = page.getByRole("article", { name: "X2", exact: true });
    await first.getByRole("button", { name: "X2 查看证据与复核" }).click();
    await expect(first.locator(".presales-row-details > .presales-evidence blockquote")).toHaveText("days.");
    await first.getByText("原人工草稿", { exact: true }).click();
    await expect(first.locator(".presales-original").first()).toContainText("Retention");
    await page.screenshot({ path: info.outputPath(`manual-reviewed-${width}.png`), fullPage: true });
  });
}
