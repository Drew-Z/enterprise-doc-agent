import { expect, test, type Page, type TestInfo } from "@playwright/test";

const documentId = "33333333-3333-4333-8333-333333333333";
const versionId = "22222222-2222-4222-8222-222222222222";
const generationId = "44444444-4444-4444-8444-444444444444";

const inventoryItem = {
  documentId,
  title: "Security policy",
  accessMode: "restricted",
  canManage: true,
  versionId,
  versionNumber: 2,
  filename: "security-policy.pdf",
  mediaType: "application/pdf",
  sizeBytes: 524_288,
  versionStatus: "ready",
  generationId,
  ingestionStatus: "succeeded",
  ingestionStage: "ready",
  errorCode: null,
  createdAt: "2026-08-23T04:30:00Z",
  updatedAt: "2026-08-24T05:45:00Z",
};

async function mockDocumentAccess(page: Page) {
  let accessMode: "tenant" | "restricted" = "restricted";
  let grants: Array<Record<string, string | null>> = [];
  await page.addInitScript(() => {
    sessionStorage.setItem("enterprise-doc.upload-token.v1", "local-token");
    localStorage.setItem("enterprise-doc-agent.locale", "en");
  });
  await page.route("**/health/ready", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      status: "ready",
      checks: { database: { status: "up" }, redis: { status: "up" }, object_store: { status: "up" } },
    }),
  }));
  await page.route("**/api/session", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      tenantId: "88888888-8888-4888-8888-888888888888",
      actorId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaab",
      role: "owner",
      capabilities: {
        documentRead: true,
        documentWrite: true,
        agentRunCreate: true,
        auditRead: true,
        auditExport: true,
        approvalDecide: true,
      },
    }),
  }));
  await page.route("**/api/documents?limit=200", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify([{ ...inventoryItem, accessMode }]),
  }));
  await page.route(`**/api/documents/${documentId}/access`, async (route) => {
    if (route.request().method() === "PUT") {
      const payload = route.request().postDataJSON() as { accessMode: "tenant" | "restricted" };
      accessMode = payload.accessMode;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ documentId, accessMode, canManage: true }),
    });
  });
  await page.route(`**/api/documents/${documentId}/grants`, async (route) => {
    if (route.request().method() === "POST") {
      const payload = route.request().postDataJSON() as { granteeRole?: string; granteeUserId?: string };
      grants = [{
        grantId: "77777777-7777-4777-8777-777777777777",
        documentId,
        granteeUserId: payload.granteeUserId ?? null,
        granteeRole: payload.granteeRole ?? null,
      }];
      await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(grants[0]) });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(grants) });
  });
}

async function exercisePolicyDrawer(page: Page, testInfo: TestInfo, screenshotName: string) {
  await page.goto("/#/documents");
  await expect(page.getByRole("heading", { level: 1, name: "Documents" })).toBeVisible();
  await page.getByRole("button", { name: "Manage access" }).first().click();
  const drawer = page.getByRole("dialog", { name: "Access policy" });
  await expect(drawer).toBeVisible();

  const accessControl = drawer.getByRole("group", { name: "Access" });
  await accessControl.getByRole("button", { name: "Tenant" }).click();
  await expect(accessControl.getByRole("button", { name: "Tenant" })).toHaveAttribute("aria-pressed", "true");

  const targetControl = drawer.getByRole("group", { name: "Grant target type" });
  await targetControl.getByRole("button", { name: "Tenant role" }).click();
  await drawer.getByRole("button", { name: "Add grant" }).click();
  await expect(drawer.locator(".grant-list strong", { hasText: "Member" })).toBeVisible();
  await expect(page.locator("html")).toHaveJSProperty("scrollWidth", page.viewportSize()?.width);
  await page.screenshot({ path: testInfo.outputPath(screenshotName), fullPage: true });
}

test("document access policy works on desktop", async ({ page }, testInfo) => {
  await mockDocumentAccess(page);
  await exercisePolicyDrawer(page, testInfo, "document-access-desktop.png");
});

test("document access policy fits a mobile viewport", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockDocumentAccess(page);
  await exercisePolicyDrawer(page, testInfo, "document-access-mobile.png");
});
