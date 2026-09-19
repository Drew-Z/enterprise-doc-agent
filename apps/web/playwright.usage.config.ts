import { defineConfig } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const webDirectory = path.dirname(fileURLToPath(import.meta.url));

// Frontend boundary acceptance only: no API, database, IdP or model server is started.
export default defineConfig({
  testDir: "./usage-e2e",
  timeout: 45_000,
  expect: { timeout: 10_000 },
  workers: 1,
  fullyParallel: false,
  reporter: [["list"]],
  outputDir: process.env.TENANT_USAGE_E2E_OUTPUT_DIR ?? path.join(tmpdir(), `tenant-usage-e2e-${randomUUID()}`),
  use: {
    baseURL: "http://127.0.0.1:5187",
    browserName: "chromium",
    headless: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    viewport: { width: 1440, height: 1100 },
  },
  webServer: {
    command: "pnpm exec vite --host 127.0.0.1 --port 5187 --strictPort",
    cwd: webDirectory,
    url: "http://127.0.0.1:5187",
    reuseExistingServer: false,
    timeout: 60_000,
    env: { VITE_AUTH_MODE: "browser", VITE_API_BASE_URL: "" },
  },
});
