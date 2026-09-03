import { expect, test, type APIRequestContext, type Browser } from "@playwright/test";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

import { parseRuntime, runtimeFile } from "./runtime";

const apiBaseUrl = "http://127.0.0.1:8000";

function authHeaders(token: string): Record<string, string> {
  return { Accept: "application/json", Authorization: `Bearer ${token}` };
}

async function createUploadedDocument(request: APIRequestContext, token: string): Promise<{
  documentId: string;
  filename: string;
}> {
  const filename = `acl-e2e-${Date.now().toString(36)}.txt`;
  const bytes = Buffer.alloc(5 * 1024 * 1024, 0x61);
  const sha256 = createHash("sha256").update(bytes).digest("hex");
  const create = await request.post(`${apiBaseUrl}/api/upload-sessions`, {
    headers: { ...authHeaders(token), "Content-Type": "application/json", "Idempotency-Key": `acl-${Date.now()}` },
    data: { filename, sizeBytes: bytes.length, mediaType: "text/plain", sha256 },
  });
  expect(create.status()).toBe(201);
  const session = await create.json() as { sessionId: string };

  const presign = await request.post(`${apiBaseUrl}/api/upload-sessions/${session.sessionId}/parts/1/presign`, {
    headers: { ...authHeaders(token), "Content-Type": "application/json" },
    data: { sizeBytes: bytes.length, checksumSha256: Buffer.from(sha256, "hex").toString("base64") },
  });
  expect(presign.status()).toBe(200);
  const part = await presign.json() as { url: string; headers: Record<string, string> };
  const objectUpload = await request.put(part.url, { headers: part.headers, data: bytes });
  expect(objectUpload.ok()).toBeTruthy();
  const complete = await request.post(`${apiBaseUrl}/api/upload-sessions/${session.sessionId}/complete`, {
    headers: { ...authHeaders(token), "Content-Type": "application/json" },
    data: {
      parts: [{
        partNumber: 1,
        sizeBytes: bytes.length,
        etag: objectUpload.headers().etag ?? "",
        checksumSha256: Buffer.from(sha256, "hex").toString("base64"),
      }],
    },
  });
  expect(complete.status(), await complete.text()).toBe(200);
  const result = await complete.json() as { documentId: string };
  return { documentId: result.documentId, filename };
}

async function authenticatedPage(browser: Browser, token: string) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await context.addInitScript((localToken) => {
    sessionStorage.setItem("enterprise-doc.upload-token.v1", localToken);
  }, token);
  return { context, page: await context.newPage() };
}

test("owner and member browsers enforce a real document grant and live revocation", async ({ browser, request }) => {
  const runtime = parseRuntime(JSON.parse(readFileSync(runtimeFile, "utf8")) as unknown);
  const document = await createUploadedDocument(request, runtime.token);
  const owner = await authenticatedPage(browser, runtime.token);
  const member = await authenticatedPage(browser, runtime.memberToken);

  try {
    await owner.page.goto("/#/documents");
    await expect(owner.page.getByRole("table").getByText(document.filename)).toBeVisible();
    await owner.page.getByRole("button", { name: "Manage access" }).first().click();
    const drawer = owner.page.getByRole("dialog", { name: "Access policy" });
    await expect(drawer).toBeVisible();
    await drawer.getByRole("button", { name: "Restricted" }).click();
    await drawer.getByRole("group", { name: "Grant target type" }).getByRole("button", { name: "User" }).click();
    await drawer.getByLabel("User ID").fill(runtime.memberActorId);
    await drawer.getByRole("button", { name: "Add grant" }).click();
    await expect(drawer.getByText(runtime.memberActorId)).toBeVisible();
    await drawer.getByRole("button", { name: "Close" }).click();

    await member.page.goto("/#/documents");
    await expect(member.page.getByRole("table").getByText(document.filename)).toBeVisible();

    await owner.page.getByRole("button", { name: "Manage access" }).first().click();
    const revokeDrawer = owner.page.getByRole("dialog", { name: "Access policy" });
    await expect(revokeDrawer.getByText(runtime.memberActorId)).toBeVisible();
    await revokeDrawer.getByRole("button", { name: "Remove grant" }).click();
    await expect(revokeDrawer.getByText(runtime.memberActorId)).toHaveCount(0);
    await revokeDrawer.getByRole("button", { name: "Close" }).click();

    await member.page.reload();
    await expect(member.page.getByRole("table").getByText(document.filename)).toHaveCount(0);
    await expect(member.page.getByRole("heading", { name: "No ready documents yet" })).toBeVisible();
  } finally {
    await owner.context.close();
    await member.context.close();
  }
});
