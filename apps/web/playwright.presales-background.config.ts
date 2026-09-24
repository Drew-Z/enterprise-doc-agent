import { defineConfig } from "@playwright/test";
import base from "./playwright.presales.config";

process.env.PRESALES_BACKGROUND_E2E = "true";

export default defineConfig({
  ...base, testMatch: "background.spec.ts",
  webServer: Array.isArray(base.webServer) ? base.webServer.map((server, index) => index === 0 ? {
    ...server, command: server.command.replace("tests.presales.browser_server", "tests.presales.background_server"),
  } : server) : base.webServer,
});
