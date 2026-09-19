import { expect, test, type APIResponse, type BrowserContext, type Locator, type Page, type Request } from "@playwright/test";
import { writeFile } from "node:fs/promises";
import { setTimeout } from "node:timers/promises";
import { z } from "zod";

export type KeycloakDriver = {
  control: (route: string) => Promise<APIResponse>;
  webOrigin: string;
};

const accountSchema = z.object({
  origin: z.string().url(), email: z.string().email(), password: z.string().min(32),
  newPassword: z.string().min(32), messageOffset: z.number().int().min(0).max(20),
});
type Account = z.infer<typeof accountSchema>;

async function account(driver: KeycloakDriver, key: "owner" | "desktop"): Promise<Account> {
  const parsed = accountSchema.safeParse(await (await driver.control("/test/keycloak/account/" + key)).json());
  if (!parsed.success) throw new Error("Invalid private identity fixture response.");
  const value = parsed.data;
  if (new URL(value.origin).hostname !== "localhost" || new URL(driver.webOrigin).hostname !== "127.0.0.1") {
    throw new Error("Product and identity fixtures require separate loopback hosts.");
  }
  return value;
}

async function secret(input: Locator, value: string) {
  await input.evaluate((element, text) => {
    if (!(element instanceof HTMLInputElement)) throw new Error("Password input missing.");
    const descriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");
    if (!descriptor?.set) throw new Error("Password input setter unavailable.");
    descriptor.set.call(element, text);
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
  }, value);
}

function observeCookies(context: BrowserContext, identityOrigin: string) {
  const checks: Promise<boolean>[] = [];
  const listener = (request: Request) => {
    if (new URL(request.url()).origin === identityOrigin) {
      checks.push(request.allHeaders().then(headers => !headers.cookie?.includes("__Host-docagent")));
    }
  };
  context.on("request", listener);
  return {
    stop: () => context.off("request", listener),
    verify: async () => {
      const result = await Promise.all(checks);
      expect(result.length).toBeGreaterThan(0);
      expect(result.every(Boolean)).toBe(true);
      return { identityRequestsObserved: result.length, applicationCookiesAtIdentity: 0 };
    },
  };
}

async function openLogin(page: Page, driver: KeycloakDriver, value: Account) {
  await page.goto(driver.webOrigin + "/#/documents");
  const authorization = page.waitForRequest(request => {
    const url = new URL(request.url());
    return url.origin === value.origin && url.pathname.endsWith("/protocol/openid-connect/auth");
  });
  await page.getByRole("link", { name: "使用企业账号登录" }).click();
  const request = await authorization;
  const url = new URL(request.url());
  const valid = url.searchParams.get("code_challenge_method") === "S256"
    && url.searchParams.get("response_type") === "code"
    && url.searchParams.get("redirect_uri") === driver.webOrigin + "/auth/callback"
    && Boolean(url.searchParams.get("code_challenge"))
    && Boolean(url.searchParams.get("state")) && Boolean(url.searchParams.get("nonce"));
  expect(valid).toBe(true);
  await expect(page.locator("#username")).toBeVisible();
}

async function followMail(page: Page, driver: KeycloakDriver, key: "owner" | "desktop", value: Account) {
  const schema = z.discriminatedUnion("available", [
    z.object({ available: z.literal(false) }),
    z.object({ available: z.literal(true), link: z.string().url() }),
  ]);
  for (let attempt = 0; attempt < 80; attempt++) {
    const parsed = schema.safeParse(await (await driver.control("/test/keycloak/mail/" + key + "?offset=" + value.messageOffset)).json());
    if (!parsed.success) throw new Error("Invalid private mailbox fixture response.");
    if (parsed.data.available) {
      const link = new URL(parsed.data.link);
      if (link.origin !== value.origin || !link.pathname.includes("/login-actions/")) {
        throw new Error("Unexpected identity action origin.");
      }
      // The action URL stays in memory and does not become a logged goto argument.
      await page.evaluate(target => { window.location.assign(target); }, parsed.data.link);
      return;
    }
    await setTimeout(250);
  }
  throw new Error("Private identity message did not arrive.");
}

export async function signInKeycloak(page: Page, label: string, driver: KeycloakDriver) {
  const key = label === "企业管理员" ? "owner" : label === "桌面受邀同事" ? "desktop" : null;
  if (!key) throw new Error("Unknown real identity test account.");
  const value = await account(driver, key);
  const cookies = observeCookies(page.context(), value.origin);
  let stage = "login";
  try {
    await openLogin(page, driver, value);
    await page.screenshot({ path: test.info().outputPath("keycloak-" + key + "-login.png") });
    await page.locator("#username").fill(value.email);
    await secret(page.locator("#password"), value.password);
    await page.locator("#kc-login").click();
    stage = "email-verification";
    await expect(page.getByText(/You need to verify your email address/i)).toBeVisible();
    const unverified = await page.request.get(driver.webOrigin + "/auth/session");
    expect(unverified.status()).toBe(200);
    expect(await unverified.json()).toEqual({ status: "anonymous" });
    await followMail(page, driver, key, value);
    stage = "product-callback";
    await expect(page.getByRole("heading", { name: "选择企业", exact: true })).toBeVisible();
    const network = await cookies.verify();
    await writeFile(test.info().outputPath("keycloak-" + key + "-evidence.json"), JSON.stringify({
      identityProvider: "keycloak", codeFlowS256Observed: true, unverifiedProductSessionDenied: true,
      verificationEmailFollowed: true, productCallbackCompleted: true, ...network,
    }, null, 2) + "\n");
  } catch {
    throw new Error("Real Keycloak sign-in failed during " + stage + ".");
  } finally {
    cookies.stop();
  }
}

export async function resetKeycloakPassword(page: Page, driver: KeycloakDriver) {
  const value = await account(driver, "owner");
  // Application logout does not promise IdP logout; force a fresh IdP browser login here.
  await page.context().clearCookies({ domain: "localhost" });
  const cookies = observeCookies(page.context(), value.origin);
  let stage = "reset-request";
  try {
    await openLogin(page, driver, value);
    await page.getByRole("link", { name: /forgot password/i }).click();
    await page.locator("#username").fill(value.email);
    await page.locator('input[type="submit"], button[type="submit"]').click();
    await followMail(page, driver, "owner", value);
    stage = "change-password";
    await expect(page.locator("#password-new")).toBeVisible();
    await page.screenshot({ path: test.info().outputPath("keycloak-password-reset.png") });
    await secret(page.locator("#password-new"), value.newPassword);
    await secret(page.locator("#password-confirm"), value.newPassword);
    await page.locator('input[type="submit"], button[type="submit"]').click();
    stage = "product-callback";
    await expect(page.getByRole("heading", { name: "选择企业", exact: true })).toBeVisible();
    return { resetEmailFollowed: true, productCallbackCompleted: true, ...await cookies.verify() };
  } catch {
    throw new Error("Real Keycloak reset failed during " + stage + ".");
  } finally {
    cookies.stop();
  }
}

export async function rejectOldPasswordAndSignIn(page: Page, driver: KeycloakDriver) {
  const value = await account(driver, "owner");
  const cookies = observeCookies(page.context(), value.origin);
  let stage = "old-password";
  try {
    await openLogin(page, driver, value);
    await page.locator("#username").fill(value.email);
    await secret(page.locator("#password"), value.password);
    await page.locator("#kc-login").click();
    await expect(page.getByText(/invalid username or password/i)).toBeVisible();
    const denied = await page.request.get(driver.webOrigin + "/auth/session");
    expect(denied.status()).toBe(200);
    expect(await denied.json()).toEqual({ status: "anonymous" });
    stage = "new-password";
    await secret(page.locator("#password"), value.newPassword);
    await page.locator("#kc-login").click();
    await expect(page.getByRole("heading", { name: "选择企业", exact: true })).toBeVisible();
    return { oldPasswordRejected: true, newPasswordAccepted: true, ...await cookies.verify() };
  } catch {
    throw new Error("Real Keycloak reauthentication failed during " + stage + ".");
  } finally {
    cookies.stop();
  }
}
