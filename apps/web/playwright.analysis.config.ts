import { defineConfig } from "@playwright/test";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "../..");
const python = path.join(root, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const outputDir = process.env.ANALYSIS_E2E_OUTPUT_DIR;
if (!outputDir) throw new Error("Set ANALYSIS_E2E_OUTPUT_DIR to an owned evidence directory.");

export default defineConfig({
  testDir: "./analysis-e2e", outputDir, fullyParallel: false, workers: 1,
  timeout: 60_000, expect: { timeout: 15_000 }, retries: 0,
  reporter: [["list"], ["json", { outputFile: path.join(outputDir, "results.json") }]],
  globalTeardown: "./analysis-e2e/teardown.ts",
  use: { baseURL: "http://127.0.0.1:18074", browserName: "chromium", headless: true,
    viewport: { width: 1440, height: 1000 }, locale: "zh-CN", trace: "retain-on-failure" },
  webServer: [
    { command: `"${python}" -X utf8 -B -m tests.analysis.browser_server`, cwd: root,
      url: "http://127.0.0.1:18766/health/live", reuseExistingServer: false, timeout: 60_000 },
    { command: "pnpm --filter web exec vite --config vite.analysis.config.ts", cwd: root,
      url: "http://127.0.0.1:18074", reuseExistingServer: false, timeout: 60_000,
      env: { VITE_API_BASE_URL: "" } },
  ],
});
