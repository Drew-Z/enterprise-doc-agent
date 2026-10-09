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
