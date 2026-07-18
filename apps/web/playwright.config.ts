import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";
import path from "node:path";

const webDirectory = path.dirname(fileURLToPath(import.meta.url));
const rootDirectory = path.resolve(webDirectory, "../..");

export default defineConfig({
  testDir: "./e2e",
  timeout: 180_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  globalSetup: "./e2e/globalSetup.ts",
  globalTeardown: "./e2e/globalTeardown.ts",
  use: {
    baseURL: "http://127.0.0.1:5173",
    browserName: "chromium",
    headless: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    viewport: { width: 1440, height: 900 },
  },
  webServer: [
    {
      command: "uv run enterprise-doc-api",
      cwd: rootDirectory,
      url: "http://127.0.0.1:8000/health/live",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: "pnpm --filter web dev",
      cwd: rootDirectory,
      url: "http://127.0.0.1:5173",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        VITE_API_BASE_URL: "http://127.0.0.1:8000",
        VITE_OBJECT_STORE_ORIGINS: "http://127.0.0.1:9000",
      },
    },
  ],
});
