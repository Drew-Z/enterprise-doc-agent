import { expect, test } from "@playwright/test";
import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";

import { parseRuntime, runtimeFile } from "./runtime";

// CI starts an isolated API with the S3-minimum 5 MiB part size so this
// scenario still exercises multipart recovery without spending two minutes
// hashing a 16 MiB browser fixture on a shared runner.
const FILE_SIZE = (process.env.CI ? 5 : 16) * 1024 * 1024 + 1024;

async function captureEvidenceScreenshot(
  page: import("@playwright/test").Page,
  path: string,
): Promise<void> {
  await page.screenshot({
    path,
    fullPage: true,
    mask: [
      page.locator("#local-api-token"),
      page.locator("#upload-file"),
      page.locator(".upload-status p"),
      page.locator(".completion-result dd"),
    ],
    maskColor: "#d7dde0",
  });
}

async function expectStableLayout(page: import("@playwright/test").Page): Promise<void> {
  const result = await page.evaluate(() => {
    const selectors = [
      ".upload-heading",
      ".auth-strip",
      ".upload-command-bar",
      ".upload-status",
      ".upload-alert",
      ".completion-result",
      ".part-section",
      ".readiness-section",
    ];
    const bands = selectors
      .map((selector) => document.querySelector<HTMLElement>(selector))
      .filter((element): element is HTMLElement => element !== null)
      .map((element) => ({ selector: `.${element.classList[0] ?? "unknown"}`, rect: element.getBoundingClientRect() }));
    const overlaps: string[] = [];
    for (let index = 1; index < bands.length; index += 1) {
      const previous = bands[index - 1];
      const current = bands[index];
      if (previous !== undefined && current !== undefined && previous.rect.bottom > current.rect.top + 1) {
        overlaps.push(`${previous.selector}/${current.selector}`);
      }
    }
    return {
      overlaps,
      dimensions: {
        viewport: document.documentElement.clientWidth,
        content: document.documentElement.scrollWidth,
      },
    };
  });
  expect(result.overlaps).toEqual([]);
  const dimensions = result.dimensions;
  return expect(dimensions.content).toBeLessThanOrEqual(dimensions.viewport);
}

test("interrupts, reloads, rejects a wrong file, resumes missing parts, and completes", async ({
  page,
}, testInfo) => {
  const runtime = parseRuntime(JSON.parse(readFileSync(runtimeFile, "utf8")) as unknown);
  const suffix = Date.now().toString(36);
  const filename = `playwright-${suffix}.txt`;
  const originalPath = testInfo.outputPath("original", filename);
  const wrongPath = testInfo.outputPath("wrong", filename);
  mkdirSync(path.dirname(originalPath), { recursive: true });
  mkdirSync(path.dirname(wrongPath), { recursive: true });
  writeFileSync(originalPath, Buffer.alloc(FILE_SIZE, 0x61));
  writeFileSync(wrongPath, Buffer.alloc(FILE_SIZE, 0x62));
  let releaseUploads = (): void => undefined;
  let interceptedPuts = 0;
  const uploadGate = new Promise<void>((resolve) => {
    releaseUploads = resolve;
  });

  await page.route("http://127.0.0.1:9000/**", async (route) => {
    if (route.request().method() === "PUT") {
      interceptedPuts += 1;
      await uploadGate;
    }
    try {
      await route.continue();
    } catch {
      // Pausing aborts an XHR that may still be held by this deterministic route gate.
    }
  });

  await page.goto("/");
  await page.getByRole("button", { name: "Documents", exact: true }).click();
  await page.getByRole("button", { name: "Open development access" }).click();
  await page.getByLabel("Local API token").fill(runtime.token);
  await page.getByRole("button", { name: "Save token" }).click();
  const fileInput = page.locator("#upload-file");
  await expect(fileInput).toBeEnabled();
  const firstPartPut = page.waitForRequest(
    (request) => request.method() === "PUT" && request.url().startsWith("http://127.0.0.1:9000/"),
    { timeout: 120_000 },
  );
  await fileInput.setInputFiles(originalPath);
  await firstPartPut;
  await expect(page.getByRole("button", { name: "Pause upload" })).toBeVisible();
  await expect.poll(() => interceptedPuts).toBeGreaterThan(0);
  await page.getByRole("button", { name: "Pause upload" }).click();
  await expect(page.getByText("Upload paused")).toBeVisible();
  releaseUploads();
  await expectStableLayout(page);
  await captureEvidenceScreenshot(page, testInfo.outputPath("upload-paused-1440x900.png"));

  await page.reload();
  await expect(page.getByText("Reselect original file")).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator("#upload-file").setInputFiles(wrongPath);
  await expect(page.getByRole("alert")).toContainText("does not match the upload session");
  await expectStableLayout(page);
  await captureEvidenceScreenshot(page, testInfo.outputPath("wrong-file-390x844.png"));

  await page.reload();
  await expect(page.getByText("Reselect original file")).toBeVisible();
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.locator("#upload-file").setInputFiles(originalPath);

  await expect(page.getByText("Upload complete")).toBeVisible({ timeout: 120_000 });
  await expect(page.getByText("Document ID")).toBeVisible();
  await expect(page.getByText("Version ID")).toBeVisible();
  await expectStableLayout(page);
  await captureEvidenceScreenshot(page, testInfo.outputPath("upload-complete-1440x900.png"));
  rmSync(originalPath, { force: true });
  rmSync(wrongPath, { force: true });
});
