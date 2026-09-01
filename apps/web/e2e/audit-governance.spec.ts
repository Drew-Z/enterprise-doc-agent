import { expect, test } from "@playwright/test";

const tenantId = "88888888-8888-4888-8888-888888888888";
const actorId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaab";
const holdId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";

const session = {
  tenantId,
  actorId,
  role: "owner",
  capabilities: {
    documentRead: true,
    documentWrite: true,
    agentRunCreate: true,
    auditRead: true,
    auditExport: true,
    approvalDecide: true,
  },
};

const hold = {
  holdId,
  tenantId,
  name: "Quarterly review",
  reason: "Preserve evidence while the control review is open.",
  resourceType: null,
  resourceId: null,
  startsAt: "2026-08-26T00:00:00+00:00",
  expiresAt: null,
  releasedAt: null,
  createdBy: actorId,
  releasedBy: null,
};

async function mockOwnerAudit(page: import("@playwright/test").Page) {
  await page.addInitScript(() => {
    sessionStorage.setItem("enterprise-doc.upload-token.v1", "local-token");
    localStorage.setItem("enterprise-doc-agent.locale", "en");
  });
  await page.route("**/health/ready", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ status: "ready", checks: { database: { status: "up" }, redis: { status: "up" }, object_store: { status: "up" } } }),
  }));
  await page.route("**/api/session", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(session),
  }));
  await page.route("**/api/audit-events**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      items: [{
        eventId: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        tenantId,
        actorId,
        action: "audit.legal_hold.created",
        resourceType: "audit_legal_hold",
        resourceId: holdId,
        occurredAt: "2026-08-26T01:00:00Z",
        requestId: null,
        correlationId: null,
        metadata: { name: hold.name },
        schemaVersion: 1,
      }],
      nextCursor: null,
    }),
  }));
  await page.route("**/api/audit-governance/retention-policy", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ tenantId, retentionDays: 365, isEnabled: true, updatedBy: actorId }),
  }));
  await page.route("**/api/audit-governance/retention-preview", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ cutoffAt: "2025-08-26T00:00:00+00:00", eligibleEventCount: 12, protectedEventCount: 2 }),
  }));
  await page.route("**/api/audit-governance/legal-holds", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify([hold]),
  }));
}

test("owner audit governance panel fits desktop and mobile layouts", async ({ page }) => {
  await mockOwnerAudit(page);
  await page.goto("/#/audit");
  await expect(page.getByRole("heading", { level: 2, name: "Audit governance" })).toBeVisible();
  await expect(page.getByRole("spinbutton", { name: "Retention days" })).toHaveValue("365");
  await expect(page.getByText("Quarterly review")).toBeVisible();

  const governanceGrid = page.locator(".audit-governance-grid");
  const cards = await governanceGrid.locator(".audit-governance-card").evaluateAll((elements) =>
    elements.map((element) => {
      const rect = element.getBoundingClientRect();
      return { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom };
    }),
  );
  expect(cards).toHaveLength(2);
  expect(cards[0]?.right ?? 0).toBeLessThanOrEqual(cards[1]?.left ?? 0);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await expect(page.getByRole("heading", { level: 2, name: "Audit governance" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Create hold" })).toBeVisible();
  await expect(page.locator("html")).toHaveJSProperty("scrollWidth", 390);
});
