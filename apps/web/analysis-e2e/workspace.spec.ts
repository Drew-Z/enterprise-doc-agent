import { expect, test } from "@playwright/test";
import { z } from "zod";

test("prepare, analyze, review, deliver, reload and use a narrow viewport", async ({ page, request }, testInfo) => {
  const response = await request.get("http://127.0.0.1:18766/__analysis_test__/context", { headers: { "X-Analysis-Test": "analysis-browser" } });
  const context = z.object({ token: z.string(), versionId: z.string().uuid() }).parse(await response.json());
  await page.addInitScript(({ token }) => { sessionStorage.setItem("enterprise-doc.upload-token.v1", token); localStorage.setItem("enterprise-doc-agent.locale", "zh"); }, context);
  await page.goto("/#/analysis");
  await expect(page.getByRole("heading", { name: /多文档审阅|Document review/ })).toBeVisible();
  const copy = { name: "项目名称", group: "分组: contract.txt", save: "保存草稿", freeze: "确认资料版本", project: "选择项目" };
  await page.getByLabel(copy.name, { exact: true }).fill("合成资料 · 方案审阅演示");
  await page.getByLabel(copy.group, { exact: true }).selectOption("supplier_a");
  await page.getByRole("button", { name: copy.save, exact: true }).click();
  await expect(page.getByRole("button", { name: copy.freeze, exact: true })).toBeEnabled();
  await page.getByRole("button", { name: copy.freeze, exact: true }).click();
  await expect(page.getByText("实施费 1200 元", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "开始取证／查看本版任务" }).click();
  const matrix = page.getByRole("region", { name: "取证矩阵" });
  await expect(matrix.getByRole("status")).toContainText("取证完成 · 10/10", { timeout: 30_000 });
  await expect(matrix.getByText("有原文依据", { exact: true })).toHaveCount(1);
  await expect(matrix.getByText("证据不足", { exact: true })).toHaveCount(9);
  await matrix.getByText(/contract.txt · TXT/).click();
  await expect(matrix.locator("blockquote")).toContainText("实施费 1200 元");
  const selected = await page.getByLabel(copy.project, { exact: true }).inputValue();
  await page.screenshot({ path: testInfo.outputPath("desktop.png"), fullPage: true });
  await page.reload();
  await page.getByLabel(copy.project, { exact: true }).selectOption(selected);
  await expect(matrix.getByRole("status")).toContainText("取证完成 · 10/10");
  const historySelect = matrix.getByRole("combobox", { name: "历史任务", exact: true });
  await expect(historySelect).toBeVisible();
  const originalRunId = await historySelect.inputValue();
  expect(originalRunId).toMatch(/^[0-9a-f-]{36}$/);
  await page.getByRole("button", { name: "开始取证／查看本版任务" }).click();
  await expect(matrix.getByRole("status")).toContainText("模型请求 10/20");

  const setupFee = matrix.getByRole("row", { name: /一次性费用/ });
  await setupFee.getByText("修订与审核", { exact: true }).click();
  await setupFee.getByLabel("确认值", { exact: true }).fill("买方确认：实施费 1200 元");
  await setupFee.getByLabel("修订理由", { exact: true }).fill("买方核对原文后补充确认。");
  await setupFee.getByRole("button", { name: "保存为新版本" }).click();
  await expect(setupFee.getByText("买方确认：实施费 1200 元", { exact: true })).toBeVisible();

  for (const row of await matrix.locator("tbody tr").all()) {
    const panel = row.locator("details.analysis-review-panel");
    if (!(await panel.evaluate(element => (element as HTMLDetailsElement).open))) {
      await panel.locator("summary").click();
    }
    await panel.getByLabel("审核理由", { exact: true }).fill("已核对当前版本及待确认状态。");
    await panel.getByRole("button", { name: "批准此版本" }).click();
    await expect(panel.getByText("此版本：approved", { exact: true })).toBeVisible();
  }

  const reviewedExport = matrix.getByRole("button", { name: "生成复核稿 XLSX" });
  await expect(reviewedExport).toBeEnabled();
  await reviewedExport.click();
  await expect(matrix.getByText(/reviewed-r1\.xlsx/)).toBeVisible();
  await matrix.getByRole("button", { name: "批准交付" }).click();
  await expect(matrix.getByRole("button", { name: "下载" })).toBeEnabled();
  await page.screenshot({ path: testInfo.outputPath("reviewed-desktop.png"), fullPage: true });

  await page.reload();
  await page.getByLabel(copy.project, { exact: true }).selectOption(selected);
  await expect(matrix.getByText(/reviewed-r1\.xlsx/)).toBeVisible();
  await expect(matrix.getByText(/复核稿 · r1 · approved/)).toBeVisible();
  await page.evaluate(() => {
    window.open = (url?: string | URL, target?: string, features?: string) => {
      sessionStorage.setItem("analysis-test-download", JSON.stringify({ url: String(url), target, features }));
      return null;
    };
  });
  await matrix.getByRole("button", { name: "下载" }).click();
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem("analysis-test-download"))).toContain("https://download.test/");
  const downloadIntent = await page.evaluate(() => sessionStorage.getItem("analysis-test-download"));
  if (downloadIntent === null) throw new Error("Download intent was not recorded.");
  expect(JSON.parse(downloadIntent)).toMatchObject({ target: "_blank", features: "noopener,noreferrer" });

  await page.getByLabel("分析问题: 一次性费用", { exact: true }).fill("实施费金额、币种和适用条件是什么？");
  await page.getByRole("button", { name: copy.save, exact: true }).click();
  await expect(page.getByRole("button", { name: copy.freeze, exact: true })).toBeEnabled();
  await page.getByRole("button", { name: copy.freeze, exact: true }).click();
  await expect(matrix.getByText("将以上一版同模式任务为基准，仅复用依赖指纹完全一致的结果；审核和交付批准不会继承。", { exact: true })).toBeVisible();
  await matrix.getByRole("button", { name: "开始取证／查看本版任务" }).click();
  await expect(matrix.getByRole("status")).toContainText("复用 9 · 重算 1", { timeout: 30_000 });
  await expect(matrix.getByRole("status")).toContainText("取证完成 · 10/10", { timeout: 30_000 });
  await expect(matrix.getByText("沿用上一版结果，需重新审核", { exact: true })).toHaveCount(9);
  await expect(matrix.getByText("本版重算：field_changed:setup_fee", { exact: true })).toHaveCount(1);
  const incrementalSetupFee = matrix.getByRole("row", { name: /一次性费用/ });
  await incrementalSetupFee.getByText("修订与审核", { exact: true }).click();
  await expect(incrementalSetupFee.getByRole("button", { name: "批准此版本" })).toBeVisible();
  await expect(incrementalSetupFee.getByText("此版本：approved", { exact: true })).toHaveCount(0);
  await expect(matrix.getByRole("button", { name: "生成复核稿 XLSX" })).toBeDisabled();
  await historySelect.selectOption(originalRunId);
  await expect(matrix.getByText(/reviewed-r1\.xlsx/)).toBeVisible();
  await expect(matrix.getByText(/复核稿 · r1 · approved/)).toBeVisible();
  await expect(matrix.getByRole("button", { name: "下载" })).toBeEnabled();

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(matrix.getByRole("heading", { name: "取证矩阵" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("reviewed-mobile.png"), fullPage: true });
});
