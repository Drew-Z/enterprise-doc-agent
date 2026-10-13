import { defineConfig } from "@playwright/test";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "../..");
const python = path.join(root, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const outputDir = process.env.WORKBOOK_E2E_OUTPUT_DIR;
if (!outputDir) throw new Error("Set WORKBOOK_E2E_OUTPUT_DIR to the task evidence directory.");
export default defineConfig({
  testDir: "./presales-e2e", testMatch: "workbook.spec.ts", outputDir, workers: 1, fullyParallel: false,
  globalTeardown: "./presales-e2e/workbook-teardown.ts",
  timeout: 90_000, expect: { timeout: 15_000 }, retries: 0,
  reporter: [["list"], ["json", { outputFile: path.join(outputDir, "results.json") }]],
  use: { baseURL: "http://127.0.0.1:18073", browserName: "chromium", headless: true, locale: "zh-CN", trace: "retain-on-failure" },
  webServer: [
    { command: `"${python}" -X utf8 -B -m tests.presales.workbook_browser_server`, cwd: root, url: "http://127.0.0.1:18765/health/live", reuseExistingServer: false, timeout: 60_000 },
    { command: "pnpm --filter web exec vite --config vite.presales.config.ts", cwd: root, url: "http://127.0.0.1:18073", reuseExistingServer: false, timeout: 60_000, env: { VITE_API_BASE_URL: "" } },
  ],
});
