import { expect, test, type APIRequestContext, type Page, type Request } from "@playwright/test";
import { createHash } from "node:crypto";
import { writeFileSync } from "node:fs";

const idp = "http://127.0.0.1:18768";
const testHeaders = { "X-Browser-Test": "isolated-browser-session" };
interface TestContext { admissionCode: string; tenantA: string; tenantB: string }
interface SignalAudit { received: string[]; sent: string[]; visibilityChanges: number }
declare global { interface Window { browserSignalAudit: SignalAudit } }

async function fixture(request: APIRequestContext): Promise<TestContext> {
  const response = await request.get(idp + "/test/context", { headers: testHeaders });
  if (!response.ok()) throw new Error("Local browser acceptance context is unavailable.");
  return await response.json() as TestContext;
}

async function signIn(page: Page, account = "已绑定账号") {
  await page.goto("/#/documents");
  await page.getByRole("link", { name: "使用企业账号登录" }).click();
  await expect(page.getByRole("heading", { name: "本地签名测试身份服务" })).toBeVisible();
  await page.getByRole("button", { name: account, exact: true }).click();
  if (account !== "取消登录") await expect(page.getByRole("heading", { name: "选择企业", exact: true })).toBeVisible();
}

async function selectEnterprise(page: Page, name: string) {
  await page.getByRole("button", { name: "进入 " + name, exact: true }).click();
  await expect(page.locator(".session-identity")).toContainText(name);
}

async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
}

async function storageContainsSecrets(page: Page): Promise<boolean> {
  return page.evaluate(() => {
    const values = [sessionStorage, localStorage].flatMap(storage => Object.keys(storage).map(key => storage.getItem(key) ?? ""));
    return values.some(value => /bss1_[A-Za-z0-9_-]{43}|adm1_[A-Za-z0-9_-]{43}|synthetic-upstream|csrfToken|browser-owner@example|browser-new@example/.test(value));
  });
}

test.beforeEach(async ({ context }) => {
  await context.addInitScript(() => {
    localStorage.setItem("enterprise-doc-agent.locale", "zh");
    const audit: SignalAudit = { received: [], sent: [], visibilityChanges: 0 };
    window.browserSignalAudit = audit;
    document.addEventListener("visibilitychange", () => { audit.visibilityChanges += 1; });
    const NativeChannel = window.BroadcastChannel;
    window.BroadcastChannel = class extends NativeChannel {
      constructor(name: string) {
        super(name);
        if (name === "enterprise-doc.browser-session.v1") this.addEventListener("message", event => {
          audit.received.push(event.data === "invalidate" ? "invalidate" : "unexpected");
        });
      }
      postMessage(value: unknown) {
        if (this.name === "enterprise-doc.browser-session.v1") audit.sent.push(value === "invalidate" ? "invalidate" : "unexpected");
        super.postMessage(value);
      }
    };
  });
});

// Playwright can include password inputs in failure snapshots even with tracing disabled.
test.afterEach(async ({ context }) => {
  for (const page of context.pages()) {
    if (!page.isClosed()) await page.locator('input[type="password"]').evaluateAll(inputs => {
      for (const input of inputs) { (input as HTMLInputElement).value = ""; input.removeAttribute("value"); }
    });
  }
});

test("desktop: signed login, cookie upload, refresh, delayed response, cross-tab switch and logout", async ({ page, context }, info) => {
  const pageErrors: string[] = [];
  const requestChecks: Promise<void>[] = [];
  type ApplicationObservation = { method: string; path: string; sessionCookie: boolean; context: boolean; csrf: boolean; bearer: boolean; status: number | null; failure: string | null };
  const applicationRequests: ApplicationObservation[] = [];
  const observedRequests = new Map<Request, ApplicationObservation>();
  const objectRequests: { method: string; cookie: boolean; authorization: boolean; context: boolean; csrf: boolean }[] = [];
  let objectSuccesses = 0;
  page.on("pageerror", error => pageErrors.push(error.message));
  page.on("request", request => {
    const url = new URL(request.url());
    if (url.pathname.startsWith("/api/")) {
      const observation: ApplicationObservation = { method: request.method(), path: url.pathname, sessionCookie: false, context: false, csrf: false, bearer: false, status: null, failure: null };
      observedRequests.set(request, observation);
      applicationRequests.push(observation);
      requestChecks.push(request.allHeaders().then(headers => {
        Object.assign(observation, { sessionCookie: Boolean(headers.cookie?.includes("__Host-docagent-session=")), context: Boolean(headers["x-session-context"]), csrf: Boolean(headers["x-csrf-token"]), bearer: Boolean(headers.authorization) });
      }));
    }
    if (url.port === "9000" && request.method() === "PUT") requestChecks.push(request.allHeaders().then(headers => {
      objectRequests.push({ method: request.method(), cookie: Boolean(headers.cookie), authorization: Boolean(headers.authorization), context: Boolean(headers["x-session-context"]), csrf: Boolean(headers["x-csrf-token"]) });
    }));
  });
  page.on("response", response => {
    const observation = observedRequests.get(response.request());
    if (observation) observation.status = response.status();
    if (new URL(response.url()).port === "9000" && response.request().method() === "PUT" && response.ok()) objectSuccesses += 1;
  });
  page.on("requestfailed", request => {
    const observation = observedRequests.get(request);
    if (observation) observation.failure = request.failure()?.errorText ?? "unknown";
  });
  await signIn(page);
  await expect(page.getByRole("button", { name: "进入 澄明软件" })).toBeVisible();
  await expect(page.getByRole("button", { name: "进入 远川服务" })).toBeVisible();
  await noOverflow(page);
  await page.screenshot({ path: info.outputPath("desktop-enterprises.png"), fullPage: true });
  await selectEnterprise(page, "澄明软件");

  const sessionCookie = (await context.cookies()).find(cookie => cookie.name === "__Host-docagent-session");
  expect(Boolean(sessionCookie)).toBe(true);
  const cookieFlags = sessionCookie ? { secure: sessionCookie.secure, httpOnly: sessionCookie.httpOnly, sameSite: sessionCookie.sameSite, domain: sessionCookie.domain, path: sessionCookie.path, opaque: /^bss1_[A-Za-z0-9_-]{43}$/.test(sessionCookie.value) } : null;
  expect(cookieFlags).toEqual({ secure: true, httpOnly: true, sameSite: "Lax", domain: "127.0.0.1", path: "/", opaque: true });
  expect(await page.evaluate(() => document.cookie.includes("docagent"))).toBe(false);
  await page.goto("/#/documents");
  await page.getByRole("button", { name: "上传文档", exact: true }).first().click();
  const drawer = page.getByRole("dialog", { name: "上传文档" });
  await expect(drawer.getByLabel("本地 API 令牌")).toHaveCount(0);
  const documentName = "browser-session-evidence.txt";
  const buffer = Buffer.from("Synthetic browser session acceptance.\nRetention: 30 days.\n", "utf8");
  const completed = page.waitForResponse(response => new URL(response.url()).pathname.endsWith("/complete") && response.request().method() === "POST");
  await drawer.getByLabel("选择文档", { exact: true }).setInputFiles({ name: documentName, mimeType: "text/plain", buffer });
  expect((await completed).ok()).toBe(true);
  await expect(drawer.getByRole("heading", { name: "上传完成", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "关闭上传" }).click();
  await expect(page.getByRole("row").filter({ hasText: documentName }).locator(".status-badge")).toHaveText("处理中");
  await page.reload();
  await expect(page.locator(".session-identity")).toContainText("澄明软件");
  await expect(page.getByRole("row").filter({ hasText: documentName })).toBeVisible();
  await noOverflow(page);
  await page.screenshot({ path: info.outputPath("desktop-uploaded.png"), fullPage: true });
  await page.locator("#document-search").fill("browser-session");

  const other = await context.newPage();
  await other.goto("/#/documents");
  await expect(other.locator(".session-identity")).toContainText("澄明软件");
  await expect(other.getByRole("row").filter({ hasText: documentName })).toBeVisible();
  await other.bringToFront();
  await page.bringToFront();
  await expect(page.locator("#document-search")).toHaveValue("browser-session");
  await page.locator("#document-search").fill("");
  let release!: () => void;
  let captured!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  const received = new Promise<void>(resolve => { captured = resolve; });
  let heldResponseContainedOriginal = false;
  let intercepted = false;
  await page.route("**/api/documents?*", async route => {
    if (intercepted) { await route.continue(); return; }
    intercepted = true;
    const response = await route.fetch();
    heldResponseContainedOriginal = (await response.text()).includes(documentName);
    captured();
    await held;
    try { await route.fulfill({ response }); } catch { /* The original fetch was aborted by the real session boundary. */ }
  });
  try {
    await page.getByRole("button", { name: "刷新文档", exact: true }).click();
    await received;
    await page.getByRole("button", { name: "切换企业", exact: true }).click();
    await selectEnterprise(page, "远川服务");
    await expect(other.locator(".session-identity")).toContainText("远川服务");
  } finally { release(); }
  await page.unrouteAll({ behavior: "wait" });
  expect(heldResponseContainedOriginal).toBe(true);
  await expect(page.getByText(documentName, { exact: true })).toHaveCount(0);
  await expect(other.getByText(documentName, { exact: true })).toHaveCount(0);
  const switchSignals = await other.evaluate(() => window.browserSignalAudit);
  expect(switchSignals.received).toContain("invalidate");
  await page.screenshot({ path: info.outputPath("desktop-switched.png"), fullPage: true });
  await page.getByRole("button", { name: "退出会话", exact: true }).click();
  await expect(page.getByRole("link", { name: "使用企业账号登录" })).toBeVisible();
  await expect(other.getByRole("link", { name: "使用企业账号登录" })).toBeVisible();
  await expect(other.locator(".session-identity")).toHaveCount(0);
  const finalSignals = await other.evaluate(() => window.browserSignalAudit);
  expect(finalSignals.received.filter(value => value === "invalidate").length).toBeGreaterThan(switchSignals.received.length);
  expect(finalSignals.received).not.toContain("unexpected");
  expect(await storageContainsSecrets(page)).toBe(false);
  await Promise.all(requestChecks);
  writeFileSync(info.outputPath("desktop-evidence.json"), JSON.stringify({ cookieFlags, applicationRequests, objectRequests, objectSuccesses, uploadedSha256: createHash("sha256").update(buffer).digest("hex"), heldResponseContainedOriginal, switchSignals, finalSignals, pageErrors }, null, 2));
  expect(applicationRequests.length).toBeGreaterThan(6);
  expect(applicationRequests.every(item => item.context && !item.bearer)).toBe(true);
  expect(applicationRequests.filter(item => !item.sessionCookie).every(item => item.status === null && item.failure === "net::ERR_ABORTED")).toBe(true);
  expect(applicationRequests.filter(item => item.status === 200).length).toBeGreaterThan(3);
  expect(applicationRequests.filter(item => item.method === "POST").every(item => item.csrf)).toBe(true);
  expect(objectRequests).toEqual([{ method: "PUT", cookie: false, authorization: false, context: false, csrf: false }]);
  expect(objectSuccesses).toBe(1);
  expect(pageErrors).toEqual([]);
  writeFileSync(info.outputPath("desktop-evidence.json"), JSON.stringify({ cookieFlags, applicationRequests, objectRequests, objectSuccesses, uploadedSha256: createHash("sha256").update(buffer).digest("hex"), heldResponseContainedOriginal, switchSignals, finalSignals, pageErrors }, null, 2));
});

test("narrow screen: new identity admits a company, selects it and restores the session", async ({ page, request }, info) => {
  const data = await fixture(request);
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page, "新企业账号");
  await expect(page.locator(".browser-empty")).toContainText("此账号暂无可访问的企业");
  await page.getByText("开通新企业", { exact: true }).click();
  await page.getByLabel("开通码", { exact: true }).fill(data.admissionCode);
  await page.getByLabel("企业名称", { exact: true }).fill("新知技术服务");
  const accepted = page.waitForResponse(response => new URL(response.url()).pathname === "/auth/admission/accept");
  await page.getByRole("button", { name: "确认开通", exact: true }).click();
  expect((await accepted).ok()).toBe(true);
  await expect(page.getByRole("button", { name: "进入 新知技术服务" })).toBeVisible();
  await expect(page.getByLabel("开通码", { exact: true })).toHaveValue("");
  await noOverflow(page);
  await page.screenshot({ path: info.outputPath("mobile-admitted.png"), fullPage: true });
  await selectEnterprise(page, "新知技术服务");
  await page.goto("/#/documents");
  await expect(page.locator(".session-identity")).toContainText("新知技术服务");
  await page.reload();
  await expect(page.locator(".session-identity")).toContainText("新知技术服务");
  await expect(page.getByLabel("本地 API 令牌")).toHaveCount(0);
  expect(await storageContainsSecrets(page)).toBe(false);
  await noOverflow(page);
  await page.screenshot({ path: info.outputPath("mobile-workspace.png"), fullPage: true });
  await page.getByRole("button", { name: "退出会话", exact: true }).click();
  await expect(page.getByRole("link", { name: "使用企业账号登录" })).toBeVisible();
});

test("an identity with the same email cannot inherit another subject's enterprises", async ({ page }, info) => {
  await signIn(page, "同邮箱未绑定账号");
  await expect(page.locator(".browser-tenant")).toHaveCount(0);
  await expect(page.locator(".browser-empty")).toContainText("此账号暂无可访问的企业");
  await page.screenshot({ path: info.outputPath("unbound-identity.png"), fullPage: true });
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  await expect(page.getByRole("link", { name: "使用企业账号登录" })).toBeVisible();
});

test("cancelled login returns a safe retry page and server expiry closes the workspace", async ({ page, request }, info) => {
  await signIn(page, "取消登录");
  await expect(page.getByRole("alert")).toHaveText("登录未完成，请重新登录。");
  expect(await page.evaluate(() => location.search === "" && !location.hash.includes("error="))).toBe(true);
  await noOverflow(page);
  await page.screenshot({ path: info.outputPath("login-cancelled.png"), fullPage: true });
  await signIn(page);
  await selectEnterprise(page, "澄明软件");
  expect((await request.post(idp + "/test/expire", { headers: testHeaders })).ok()).toBe(true);
  await page.goto("/#/documents");
  await expect(page.getByRole("link", { name: "使用企业账号登录" })).toBeVisible();
  await expect(page.locator(".session-identity")).toHaveCount(0);
  expect(await storageContainsSecrets(page)).toBe(false);
});
