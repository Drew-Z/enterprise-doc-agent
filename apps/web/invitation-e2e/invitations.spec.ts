import { expect, test, type Browser, type BrowserContext, type Page } from "@playwright/test";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { z } from "zod";

const origin = "http://127.0.0.1:5173";
const output = process.env.INVITATION_OUTPUT_DIR;
if (!output) throw new Error("Missing invitation acceptance output directory.");
const runDirectory: string = output;

async function prepare(context: BrowserContext) {
  await context.addInitScript(() => localStorage.setItem("enterprise-doc-agent.locale", "zh"));
  await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin });
}

async function isolatedContext(browser: Browser, mobile = false) {
  const context = await browser.newContext({ baseURL: origin, locale: "zh-CN", viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 } });
  await prepare(context);
  return context;
}

async function scrub(context: BrowserContext) {
  for (const page of context.pages()) if (!page.isClosed()) {
    await page.locator('input[type="password"]').evaluateAll(inputs => {
      for (const input of inputs) if (input instanceof HTMLInputElement) { input.value = ""; input.removeAttribute("value"); }
    }).catch(() => undefined);
  }
}

async function signIn(page: Page, account: string) {
  await page.goto("/#/documents");
  await page.getByRole("link", { name: "使用企业账号登录" }).click();
  await expect(page.getByRole("heading", { name: "本地签名测试身份服务" })).toBeVisible();
  await page.getByRole("button", { name: account, exact: true }).click();
  await expect(page.getByRole("heading", { name: "选择企业", exact: true })).toBeVisible();
}

async function selectEnterprise(page: Page, name: string) {
  await page.getByRole("button", { name: "进入 " + name, exact: true }).click();
  await expect(page.locator(".session-identity")).toContainText(name);
}

async function openMembers(page: Page) {
  await page.goto("/#/identity");
  await expect(page.getByRole("heading", { name: "邀请同事", exact: true })).toBeVisible();
  await expect(page.getByLabel("受邀邮箱", { exact: true })).toBeEnabled();
  await expect(page.getByLabel("Issuer URL", { exact: true })).toHaveCount(0);
}

async function copyInvitation(page: Page): Promise<string> {
  await page.getByRole("button", { name: "复制邀请链接", exact: true }).click();
  await expect(page.getByText(/邀请链接已复制/)).toBeVisible();
  const link = await page.evaluate(() => navigator.clipboard.readText());
  const parsed = new URL(link);
  const token = new URLSearchParams(parsed.hash.slice(parsed.hash.indexOf("?") + 1)).get("token") ?? "";
  expect(parsed.origin === origin && parsed.hash.startsWith("#/invitation?") && /^inv1_[A-Za-z0-9_-]{43}$/.test(token)).toBe(true);
  return token;
}

async function createInvitation(page: Page, email: string) {
  await page.getByLabel("受邀邮箱", { exact: true }).fill(email);
  await page.getByRole("button", { name: "生成邀请链接", exact: true }).click();
  await expect(page.getByText(/尚未发送邮件/)).toBeVisible();
  return copyInvitation(page);
}

async function openInvitation(page: Page, token: string) {
  // Keep the secret out of Playwright navigation call logs. The real initial-entry
  // parser still receives the browser fragment before the application renders.
  await page.evaluate(value => {
    history.replaceState(null, "", "/#/invitation?token=" + value);
    location.reload();
  }, token);
  await expect(page.getByRole("button", { name: "查看邀请", exact: true })).toBeVisible();
  expect(await page.evaluate(() => location.hash === "")).toBe(true);
}

async function privacyAndLayout(page: Page, tokens: string[]) {
  const result = await page.evaluate(values => {
    const stored = [localStorage, sessionStorage].flatMap(storage => Object.keys(storage).flatMap(key => [key, storage.getItem(key) ?? ""]));
    return { overflow: document.documentElement.scrollWidth - innerWidth,
      visibleSecret: values.some(value => document.body.textContent?.includes(value)),
      persistedSecret: stored.some(value => /(?:inv1_|adm1_|bss1_)[A-Za-z0-9_-]{43}|csrfToken|contextVersion/.test(value)),
      fragmentSecret: location.hash.includes("token="),
    };
  }, tokens);
  expect(result.overflow).toBeLessThanOrEqual(1);
  expect(result.visibleSecret || result.persistedSecret || result.fragmentSecret).toBe(false);
  return result;
}

function networkEvidence(pages: Page[]) {
  const observations: { method: string; path: string; status: number; context: boolean; csrf: boolean; origin: boolean; bearer: boolean; cookie: boolean; noStore: boolean; noReferrer: boolean }[] = [];
  const pending: Promise<void>[] = [];
  for (const page of pages) page.on("response", response => {
    const pathname = new URL(response.url()).pathname;
    if (!pathname.startsWith("/api/invitations") && !pathname.startsWith("/auth/invitations")) return;
    pending.push((async () => {
      const request = response.request();
      const headers = await request.allHeaders();
      const reply = await response.allHeaders();
      observations.push({ method: request.method(), path: pathname, status: response.status(),
        context: Boolean(headers["x-session-context"]), csrf: Boolean(headers["x-csrf-token"]), origin: headers.origin === origin,
        bearer: Boolean(headers.authorization), cookie: Boolean(headers.cookie?.includes("__Host-docagent-session=")),
        noStore: Boolean(reply["cache-control"]?.includes("no-store")), noReferrer: reply["referrer-policy"] === "no-referrer",
      });
    })());
  });
  return async () => {
    await Promise.all(pending);
    expect(observations.length).toBeGreaterThan(0);
    expect(observations.every(value => value.context && value.cookie && !value.bearer && value.noStore && value.noReferrer && (value.method === "GET" || value.csrf && value.origin))).toBe(true);
    return observations;
  };
}

async function state(page: Page) {
  const run = z.object({ runId: z.string() }).parse(JSON.parse(await readFile(path.join(runDirectory, "run-context.json"), "utf8")));
  const response = await page.request.get("http://127.0.0.1:18770/test/state", { headers: { "X-Invitation-Run": run.runId } });
  expect(response.ok()).toBe(true);
  return z.object({ activeMembers: z.record(z.string(), z.number()), outsiderUsers: z.number(), protocol: z.object({ pkceVerified: z.number(), applicationCookiesAtIdp: z.number() }) }).parse(await response.json());
}

test.beforeEach(async ({ context }) => { await prepare(context); });
test.afterEach(async ({ context }) => { await scrub(context); });

test("desktop: owner creates and rotates a link; colleague confirms, selects, refreshes and signs out; full seats and revoke deny entry", async ({ browser, context, page }, info) => {
  const invitedContext = await isolatedContext(browser);
  const outsiderContext = await isolatedContext(browser);
  const invited = await invitedContext.newPage();
  const outsider = await outsiderContext.newPage();
  const collectNetwork = networkEvidence([page, invited, outsider]);
  try {
    await signIn(page, "企业管理员");
    await selectEnterprise(page, "澄明软件");
    await openMembers(page);
    const oldToken = await createInvitation(page, "desktop-colleague@example.test");
    const row = page.getByRole("listitem").filter({ hasText: "desktop-colleague@example.test" });
    await row.getByRole("button", { name: "重新生成链接", exact: true }).click();
    const token = await copyInvitation(page);
    expect(token !== oldToken).toBe(true);
    await privacyAndLayout(page, [oldToken, token]);
    await page.screenshot({ path: info.outputPath("desktop-invitations.png"), fullPage: true });

    await signIn(invited, "桌面受邀同事");
    await openInvitation(invited, oldToken);
    await invited.getByRole("button", { name: "查看邀请", exact: true }).click();
    await expect(invited.getByText("邀请未通过核验，请检查原链接和当前登录账号。")).toBeVisible();
    await openInvitation(invited, token);
    await invited.getByRole("button", { name: "查看邀请", exact: true }).click();
    await expect(invited.getByRole("heading", { name: "澄明软件", exact: true })).toBeVisible();
    await invited.screenshot({ path: info.outputPath("desktop-confirm-enterprise.png"), fullPage: true });
    await invited.getByRole("button", { name: "确认加入企业", exact: true }).click();
    await expect(invited.getByText("已加入企业，请选择企业进入。若列表未更新，可重新核验。")).toBeVisible();
    await expect(invited.locator(".session-identity")).toHaveCount(0);
    await invited.screenshot({ path: info.outputPath("desktop-joined-chooser.png"), fullPage: true });
    await selectEnterprise(invited, "澄明软件");
    await invited.reload();
    await expect(invited.locator(".session-identity")).toContainText("澄明软件");
    await privacyAndLayout(invited, [oldToken, token]);
    await invited.getByRole("button", { name: "退出会话", exact: true }).click();
    await expect(invited.getByRole("link", { name: "使用企业账号登录" })).toBeVisible();

    await page.getByRole("button", { name: "刷新邀请", exact: true }).click();
    await expect(page.getByText("2 / 2", { exact: true })).toBeVisible();
    await expect(row.getByText("已接受", { exact: true })).toBeVisible();
    const fullToken = await createInvitation(page, "other-colleague@example.test");
    await signIn(outsider, "其他邮箱账号");
    await openInvitation(outsider, fullToken);
    await outsider.getByRole("button", { name: "查看邀请", exact: true }).click();
    await outsider.getByRole("button", { name: "确认加入企业", exact: true }).click();
    await expect(outsider.getByText("企业成员席位已满，请联系管理员后再试。")).toBeVisible();
    await privacyAndLayout(outsider, [fullToken]);
    await outsider.screenshot({ path: info.outputPath("desktop-seat-limit.png"), fullPage: true });
    await page.getByRole("listitem").filter({ hasText: "other-colleague@example.test" }).getByRole("button", { name: "撤销邀请", exact: true }).click();
    await expect(page.getByText("已撤销", { exact: true })).toBeVisible();
    await outsider.getByRole("button", { name: "查看邀请", exact: true }).click();
    await expect(outsider.getByText("邀请未通过核验，请检查原链接和当前登录账号。")).toBeVisible();
    const finalState = await state(page);
    expect(finalState.activeMembers["澄明软件"]).toBe(2);
    expect(finalState.outsiderUsers).toBe(0);
    expect(finalState.protocol.applicationCookiesAtIdp).toBe(0);
    const cookie = (await context.cookies()).find(value => value.name === "__Host-docagent-session");
    const cookieFlags = cookie ? { secure: cookie.secure, httpOnly: cookie.httpOnly, sameSite: cookie.sameSite, domain: cookie.domain } : null;
    expect(cookieFlags).toEqual({ secure: true, httpOnly: true, sameSite: "Lax", domain: "127.0.0.1" });
    await writeFile(path.join(runDirectory, "desktop-client-evidence.json"), JSON.stringify({ finalState, cookieFlags, network: await collectNetwork(), privacy: await privacyAndLayout(page, [token, oldToken, fullToken]) }, null, 2) + "\n", { flag: "wx" });
  } finally {
    await scrub(invitedContext); await scrub(outsiderContext);
    await invitedContext.close(); await outsiderContext.close();
  }
});

test("mobile: wrong email is denied; the invited account confirms membership and enters only after selecting the enterprise", async ({ browser, page }, info) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const recipientContext = await isolatedContext(browser, true);
  const recipient = await recipientContext.newPage();
  const collectNetwork = networkEvidence([page, recipient]);
  try {
    await signIn(page, "企业管理员");
    await selectEnterprise(page, "远川服务");
    await openMembers(page);
    const token = await createInvitation(page, "mobile-colleague@example.test");
    await privacyAndLayout(page, [token]);
    await page.screenshot({ path: info.outputPath("mobile-invitations.png"), fullPage: true });
    await signIn(recipient, "其他邮箱账号");
    await openInvitation(recipient, token);
    await recipient.getByRole("button", { name: "查看邀请", exact: true }).click();
    await expect(recipient.getByText("邀请未通过核验，请检查原链接和当前登录账号。")).toBeVisible();
    await recipient.getByRole("button", { name: "退出并更换账号", exact: true }).click();
    await expect(recipient.getByRole("link", { name: "使用企业账号登录" })).toBeVisible();
    await signIn(recipient, "手机受邀同事");
    await openInvitation(recipient, token);
    await recipient.getByRole("button", { name: "查看邀请", exact: true }).click();
    await expect(recipient.getByRole("heading", { name: "远川服务", exact: true })).toBeVisible();
    await privacyAndLayout(recipient, [token]);
    await recipient.screenshot({ path: info.outputPath("mobile-confirm-enterprise.png"), fullPage: true });
    await recipient.getByRole("button", { name: "确认加入企业", exact: true }).click();
    await expect(recipient.getByText("已加入企业，请选择企业进入。若列表未更新，可重新核验。")).toBeVisible();
    await expect(recipient.locator(".session-identity")).toHaveCount(0);
    await selectEnterprise(recipient, "远川服务");
    await recipient.reload();
    await expect(recipient.locator(".session-identity")).toContainText("远川服务");
    await privacyAndLayout(recipient, [token]);
    await recipient.screenshot({ path: info.outputPath("mobile-entered-enterprise.png"), fullPage: true });
    await recipient.getByRole("button", { name: "退出会话", exact: true }).click();
    await expect(recipient.getByRole("link", { name: "使用企业账号登录" })).toBeVisible();
    const finalState = await state(page);
    expect(finalState.activeMembers["远川服务"]).toBe(2);
    expect(finalState.outsiderUsers).toBe(0);
    expect(finalState.protocol.applicationCookiesAtIdp).toBe(0);
    await writeFile(path.join(runDirectory, "mobile-client-evidence.json"), JSON.stringify({ finalState, network: await collectNetwork(), privacy: await privacyAndLayout(recipient, [token]) }, null, 2) + "\n", { flag: "wx" });
  } finally { await scrub(recipientContext); await recipientContext.close(); }
});
