import { expect, test, type APIRequestContext, type BrowserContext, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { z } from "zod";
import { admissionReceiptSchema, browserAuthenticatedSchema, type BrowserAuthenticatedSession } from "../src/auth/schemas";

export const origin = "http://127.0.0.1:5173";
const idp = "http://127.0.0.1:18770";
const output = process.env.FIRST_USE_OUTPUT_DIR;
if (!output) throw new Error("Missing first-use output directory.");
export const runDirectory: string = output;

export function identityProvider(): "signed" | "keycloak" {
  return z.enum(["signed", "keycloak"]).parse(test.info().project.metadata.identityProvider);
}

const fileSchema = z.object({
  name: z.string(), mediaType: z.string(), sizeBytes: z.number(), sha256: z.string(),
  excerpt: z.string(), heading: z.string().nullable(), pageNumber: z.number().nullable(),
});
export const fixturesSchema = z.record(z.enum(["txt", "pdf", "docx", "broken_pdf", "repaired_pdf"]), fileSchema);
export type Fixture = z.infer<typeof fileSchema>;
export type FixtureKey = keyof z.infer<typeof fixturesSchema>;
const contextSchema = z.object({
  admissionCodes: z.object({ a: z.string(), b: z.string() }),
  fixtures: fixturesSchema,
});

export async function control(request: APIRequestContext, route: string, data?: unknown) {
  const run = z.object({ runId: z.string().regex(/^[0-9a-f]{32}$/) }).parse(
    JSON.parse(await readFile(path.join(runDirectory, "run-context.json"), "utf8")),
  );
  const headers = { "X-Invitation-Run": run.runId };
  const response = data === undefined
    ? await request.get(idp + route, { headers })
    : await request.post(idp + route, { headers, data });
  expect(response.ok(), "The local test control request should succeed.").toBe(true);
  return response;
}

export async function fixture(request: APIRequestContext) {
  const result = contextSchema.safeParse(await (await control(request, "/test/context")).json());
  if (!result.success) throw new Error("Invalid local first-use fixture context.");
  return result.data;
}

export async function prepare(context: BrowserContext) {
  await context.addInitScript(() => localStorage.setItem("enterprise-doc-agent.locale", "zh"));
  await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin });
}

export async function scrub(context: BrowserContext) {
  for (const page of context.pages()) {
    if (page.isClosed()) continue;
    await page.locator('input[type="password"]').evaluateAll(inputs => {
      for (const input of inputs) {
        if (input instanceof HTMLInputElement) {
          input.value = "";
          input.removeAttribute("value");
        }
      }
    });
  }
}

export async function signIn(page: Page, account: string) {
  if (identityProvider() === "keycloak") {
    await (await import("./keycloak")).signInKeycloak(page, account, {
      control: route => control(page.request, route), webOrigin: origin,
    });
    return;
  }
  await page.goto("/#/documents");
  await page.getByRole("link", { name: "使用企业账号登录" }).click();
  await expect(page.getByRole("heading", { name: "本地签名测试身份服务" })).toBeVisible();
  await page.getByRole("button", { name: account, exact: true }).click();
  await expect(page.getByRole("heading", { name: "选择企业", exact: true })).toBeVisible();
}

export async function admit(page: Page, code: string, name: string) {
  const input = page.getByLabel("开通码", { exact: true });
  if (!await input.isVisible()) await page.getByText("开通新企业", { exact: true }).click();
  await expect(input).toBeVisible();
  // Keep a potential Playwright action error from printing the code argument.
  await input.evaluate((element, value) => {
    if (!(element instanceof HTMLInputElement)) throw new Error("Admission code input missing.");
    const descriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");
    if (!descriptor?.set) throw new Error("Native input setter missing.");
    descriptor.set.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
  }, code);
  await page.getByLabel("企业名称", { exact: true }).fill(name);
  const accepted = page.waitForResponse(response => new URL(response.url()).pathname === "/auth/admission/accept");
  await page.getByRole("button", { name: "确认开通", exact: true }).click();
  const response = await accepted;
  expect(response.ok()).toBe(true);
  const receipt = admissionReceiptSchema.parse(await response.json());
  await expect(input).toHaveValue("");
  await expect(page.getByRole("button", { name: "进入 " + name, exact: true })).toBeVisible();
  return receipt.tenantId;
}

export async function selectEnterprise(page: Page, name: string) {
  await page.getByRole("button", { name: "进入 " + name, exact: true }).click();
  await expect(page.locator(".session-identity")).toContainText(name);
}

export async function switchEnterprise(page: Page, name: string) {
  if (!await page.getByRole("button", { name: "切换企业", exact: true }).isVisible()) {
    await page.getByRole("button", { name: "打开导航", exact: true }).click();
  }
  await page.getByRole("button", { name: "切换企业", exact: true }).click();
  await expect(page.getByRole("heading", { name: "选择企业", exact: true })).toBeVisible();
  await selectEnterprise(page, name);
}

export async function invite(page: Page, email: string) {
  await page.goto("/#/identity");
  await expect(page.getByRole("heading", { name: "邀请同事", exact: true })).toBeVisible();
  await expect(page.getByLabel("Issuer URL", { exact: true })).toHaveCount(0);
  await page.getByLabel("受邀邮箱", { exact: true }).fill(email);
  await page.getByRole("button", { name: "生成邀请链接", exact: true }).click();
  await expect(page.getByText(/尚未发送邮件/)).toBeVisible();
  await page.getByRole("button", { name: "复制邀请链接", exact: true }).click();
  await expect(page.getByText(/邀请链接已复制/)).toBeVisible();
  const link = new URL(await page.evaluate(() => navigator.clipboard.readText()));
  await page.evaluate(() => navigator.clipboard.writeText(""));
  const token = new URLSearchParams(link.hash.slice(link.hash.indexOf("?") + 1)).get("token") ?? "";
  expect(link.origin === origin && link.hash.startsWith("#/invitation?") && /^inv1_[A-Za-z0-9_-]{43}$/.test(token)).toBe(true);
  return token;
}

export async function acceptInvitation(page: Page, token: string, enterprise: string) {
  // Exercise the real fragment entry without writing the invitation into action logs.
  await page.evaluate(value => {
    history.replaceState(null, "", "/#/invitation?token=" + value);
    location.reload();
  }, token);
  await page.getByRole("button", { name: "查看邀请", exact: true }).click();
  expect(await page.evaluate(() => location.hash === "")).toBe(true);
  await expect(page.getByRole("heading", { name: enterprise, exact: true })).toBeVisible();
  await page.getByRole("button", { name: "确认加入企业", exact: true }).click();
  await expect(page.getByText("已加入企业，请选择企业进入。若列表未更新，可重新核验。")).toBeVisible();
  await expect(page.locator(".session-identity")).toHaveCount(0);
  await selectEnterprise(page, enterprise);
  await page.reload();
  await expect(page.locator(".session-identity")).toContainText(enterprise);
}

export async function session(page: Page): Promise<BrowserAuthenticatedSession> {
  const value: unknown = await page.evaluate(async () => {
    const response = await fetch("/auth/session", { credentials: "same-origin", cache: "no-store" });
    if (!response.ok) throw new Error("Browser session unavailable.");
    return await response.json() as unknown;
  });
  const parsed = browserAuthenticatedSchema.safeParse(value);
  if (!parsed.success) throw new Error("Expected an authenticated browser session.");
  return parsed.data;
}

export async function business(page: Page, route: string, options: {
  method?: "GET" | "POST"; data?: unknown; credential?: BrowserAuthenticatedSession;
} = {}) {
  if (!route.startsWith("/api/")) throw new Error("Expected an application-relative API route.");
  const credential = options.credential ?? await session(page);
  return page.evaluate(async input => {
    const headers: Record<string, string> = { "X-Session-Context": input.context };
    if (input.method !== "GET") {
      headers["X-CSRF-Token"] = input.csrf;
      headers["Content-Type"] = "application/json";
      headers["Idempotency-Key"] = crypto.randomUUID();
    }
    const response = await fetch(input.route, {
      method: input.method,
      headers,
      credentials: "same-origin",
      cache: "no-store",
      body: input.data === undefined ? undefined : JSON.stringify(input.data),
    });
    return {
      status: response.status,
      requestId: response.headers.get("X-Request-Id"),
      body: await response.json() as unknown,
    };
  }, {
    route,
    method: options.method ?? "GET",
    data: options.data,
    context: credential.contextVersion,
    csrf: credential.csrfToken,
  });
}

export async function privacyAndLayout(page: Page) {
  const result = await page.evaluate(() => {
    const values = [localStorage, sessionStorage].flatMap(storage => Object.keys(storage).flatMap(key => [key, storage.getItem(key) ?? ""]));
    return {
      overflow: document.documentElement.scrollWidth - innerWidth,
      persistedSecret: values.some(value => /(?:inv1_|adm1_|bss1_)[A-Za-z0-9_-]{43}|csrfToken|contextVersion/.test(value)),
      fragmentSecret: location.hash.includes("token="),
      readableCookie: document.cookie.includes("docagent"),
    };
  });
  expect(result.overflow).toBeLessThanOrEqual(1);
  expect(result.persistedSecret || result.fragmentSecret || result.readableCookie).toBe(false);
  return result;
}
