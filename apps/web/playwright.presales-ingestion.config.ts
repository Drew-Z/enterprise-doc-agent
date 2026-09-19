import { defineConfig } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "../..");
const python = path.join(root, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const runDir = process.env.PRESALES_INGESTION_OUTPUT_DIR ?? mkdtempSync(path.join(tmpdir(), "presales-ingestion-"));

export default defineConfig({
  testDir: "./presales-ingestion-e2e", outputDir: path.join(runDir, "test-results"),
  fullyParallel: false, workers: 1, retries: 0, timeout: 120_000,
  expect: { timeout: 30_000 },
  reporter: [["list"], ["json", { outputFile: path.join(runDir, "results.json") }]],
  globalTeardown: "./presales-ingestion-e2e/teardown.ts",
  use: { baseURL: "http://127.0.0.1:5173", browserName: "chromium", headless: true,
    viewport: { width: 1440, height: 1000 }, locale: "zh-CN", trace: "off" },
  webServer: [
    { command: `"${python}" -X utf8 -B -m tests.presales.ingestion_server`, cwd: root,
      url: "http://127.0.0.1:18766/health/live", reuseExistingServer: false, timeout: 60_000,
      env: { PRESALES_INGESTION_OUTPUT_DIR: runDir } },
    { command: "pnpm --filter web exec vite --config vite.presales-ingestion.config.ts", cwd: root,
      url: "http://127.0.0.1:5173", reuseExistingServer: false, timeout: 60_000,
      env: { VITE_API_BASE_URL: "" } },
  ],
});
