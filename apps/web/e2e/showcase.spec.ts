import { expect, test } from "@playwright/test";

const showcaseRoutes = [
  { route: "overview", heading: "Knowledge operations" },
  { route: "documents", heading: "Documents" },
  { route: "agent-runs", heading: "Agent runs" },
  { route: "audit", heading: "Audit log" },
  { route: "runtime", heading: "Runtime health" },
] as const;

function watchApiRequests(page: import("@playwright/test").Page): string[] {
  const apiRequests: string[] = [];
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (path === "/health/ready" || path.startsWith("/api/")) {
      apiRequests.push(`${request.method()} ${path}`);
    }
  });
  return apiRequests;
}

test("showcase routes render from local fixtures without API requests", async ({ page }) => {
  const apiRequests = watchApiRequests(page);

  for (const { route, heading } of showcaseRoutes) {
    await page.goto(`/?showcase=1#/${route}`);
    await expect(page.getByRole("heading", { level: 1, name: heading, exact: true })).toBeVisible();
    await expect(page.getByText("Showcase snapshot").first()).toBeVisible();
  }

  await page.goto("/?showcase=1#/runtime");
  await expect(page.getByRole("heading", { level: 2, name: "Release scope" })).toBeVisible();
  await expect(page.getByText("External gate")).toBeVisible();
  await expect(page.getByText("Deferred")).toBeVisible();

  expect(apiRequests).toEqual([]);
});

test("showcase keeps operational writes unavailable", async ({ page }) => {
  const apiRequests = watchApiRequests(page);

  await page.goto("/?showcase=1#/overview");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Open navigation" }).click();
  const mobileBrand = page.locator(".mobile-nav-drawer .sidebar-brand strong");
  await expect(mobileBrand).toBeVisible();
  expect(await mobileBrand.evaluate((element) => getComputedStyle(element).color)).not.toBe("rgb(247, 249, 251)");
  await page.getByRole("button", { name: "Close navigation" }).click();

  await page.setViewportSize({ width: 1280, height: 900 });
  await page.getByRole("button", { name: "Open Agent search" }).click();
  await expect(page.getByRole("button", { name: /Review document inventory/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Inspect Agent run/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Start an Agent run/ })).toHaveCount(0);

  await page.goto("/?showcase=1#/documents");
  await expect(page.getByRole("button", { name: "Demo only" })).toBeDisabled();

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/?showcase=1#/agent-runs");
  await expect(page.getByLabel("Document version")).toBeDisabled();
  await expect(page.getByRole("radio", { name: "Question" })).toBeDisabled();
  await expect(page.getByRole("textbox", { name: "Request", exact: true })).toBeDisabled();
  await expect(page.getByLabel("Request publication after approval")).toBeDisabled();
  await expect(page.getByRole("button", { name: "Demo only" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Download answer" })).toBeDisabled();

  const runPanel = page.locator(".showcase-agent-workspace .agent-run-panel");
  const runForm = page.locator(".showcase-agent-workspace .agent-form");
  const runPanelBox = await runPanel.boundingBox();
  const runFormBox = await runForm.boundingBox();
  expect(runPanelBox).not.toBeNull();
  expect(runFormBox).not.toBeNull();
  expect(runPanelBox?.y ?? Infinity).toBeLessThan(runFormBox?.y ?? -Infinity);

  expect(apiRequests).toEqual([]);
});

test("showcase document inventory presents complete cards on narrow screens", async ({ page }) => {
  const apiRequests = watchApiRequests(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/?showcase=1#/documents");

  await expect(page.getByText("Snapshot", { exact: true })).toBeVisible();
  await expect(page.locator(".showcase-pill-wide")).toBeHidden();
  const inventoryList = page.getByLabel("Document inventory list");
  await expect(inventoryList).toBeVisible();
  await expect(inventoryList.getByText("information-security-policy.pdf")).toBeVisible();
  await expect(inventoryList.getByText("vendor-onboarding-controls.docx")).toBeVisible();
  await expect(page.locator("html")).toHaveJSProperty("scrollWidth", 390);

  expect(apiRequests).toEqual([]);
});

test("showcase audit log presents the governance timeline on narrow screens", async ({ page }) => {
  const apiRequests = watchApiRequests(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/?showcase=1#/audit");

  await expect(page.getByRole("heading", { level: 1, name: "Audit log", exact: true })).toBeVisible();
  await expect(page.getByRole("main").getByText("Local fixture snapshot")).toBeVisible();
  await expect(page.getByRole("button", { name: "Export audit CSV" })).toBeDisabled();
  const timeline = page.getByLabel("Governance timeline list");
  await expect(timeline).toBeVisible();
  await expect(timeline.getByText("information-security-policy.pdf")).toBeVisible();
  await expect(page.locator("html")).toHaveJSProperty("scrollWidth", 390);

  expect(apiRequests).toEqual([]);
});
