import { defineConfig } from "@playwright/test";
import { existsSync } from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "../..");
const python = path.join(root, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const runDir = process.env.BROWSER_SESSION_OUTPUT_DIR;
if (!runDir || !path.isAbsolute(runDir) || !existsSync(runDir)) {
  throw new Error("BROWSER_SESSION_OUTPUT_DIR must identify an existing, dedicated acceptance directory.");
}

export default defineConfig({
  testDir: "./browser-session-e2e", outputDir: path.join(runDir, "test-results"),
  fullyParallel: false, workers: 1, retries: 0, timeout: 120_000,
  expect: { timeout: 20_000 },
  reporter: [["list"], ["json", { outputFile: path.join(runDir, "results.json") }]],
  globalTeardown: "./browser-session-e2e/teardown.ts",
  use: {
    baseURL: "http://127.0.0.1:5173", browserName: "chromium", headless: true,
    viewport: { width: 1440, height: 1000 }, locale: "zh-CN", trace: "off",
  },
  webServer: [
    {
      command: `"${python}" -X utf8 -B -m tests.browser_sessions.browser_server`, cwd: root,
      url: "http://127.0.0.1:18768/health", reuseExistingServer: false, timeout: 60_000,
      env: { BROWSER_SESSION_OUTPUT_DIR: runDir },
    },
    {
      command: "pnpm --filter web exec vite --config vite.browser-session.config.ts", cwd: root,
      url: "http://127.0.0.1:5173", reuseExistingServer: false, timeout: 60_000,
      env: { VITE_API_BASE_URL: "", VITE_AUTH_MODE: "browser" },
    },
  ],
});
