import { defineConfig } from "@playwright/test";
import { existsSync } from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "../..");
const python = path.join(root, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const runDir = process.env.FIRST_USE_OUTPUT_DIR;
if (!runDir || !path.isAbsolute(runDir) || !existsSync(runDir)) {
  throw new Error("FIRST_USE_OUTPUT_DIR must identify an existing, fresh acceptance directory.");
}
const destination: string = runDir;

export function firstUseConfig(identityProvider: "signed" | "keycloak") {
  // Playwright's automatic failure snapshot can include live Keycloak action URLs.
  if (identityProvider === "keycloak") process.env.PLAYWRIGHT_NO_COPY_PROMPT = "1";
  return defineConfig({
  metadata: { identityProvider },
  testDir: "./first-use-e2e",
  outputDir: path.join(destination, "test-results"),
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: identityProvider === "keycloak" ? 360_000 : 240_000,
  expect: { timeout: 30_000 },
  reporter: [["list"], ["json", { outputFile: path.join(destination, "results.json") }]],
  globalTeardown: "./first-use-e2e/teardown.ts",
  use: {
    baseURL: "http://127.0.0.1:5173",
    browserName: "chromium",
    headless: true,
    viewport: { width: 1440, height: 1000 },
    locale: "zh-CN",
    trace: "off",
    screenshot: "off",
    video: "off",
    actionTimeout: 15_000,
  },
  webServer: [
    {
      command: '"' + python + '" -X utf8 -B -m tests.first_use.' + (identityProvider === "keycloak" ? "keycloak_server" : "browser_server"),
      cwd: root,
      url: "http://127.0.0.1:18770/health",
      reuseExistingServer: false,
      timeout: identityProvider === "keycloak" ? 180_000 : 60_000,
      env: { FIRST_USE_OUTPUT_DIR: destination },
    },
    {
      command: "pnpm --filter web exec vite --config vite.invitation.config.ts",
      cwd: root,
      url: "http://127.0.0.1:5173",
      reuseExistingServer: false,
      timeout: 60_000,
      env: { VITE_AUTH_MODE: "browser", VITE_API_BASE_URL: "" },
    },
  ],
  });
}

export default firstUseConfig("signed");
