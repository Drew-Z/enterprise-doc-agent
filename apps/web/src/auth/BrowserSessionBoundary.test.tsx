import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { setLocale } from "../i18n";
import { BrowserSessionBoundary } from "./BrowserSessionBoundary";
import { consumeAuthEntry } from "./entry";
import { configureAuthentication, type Fetcher } from "./transport";

const tenant = { tenantId: "00000000-0000-4000-8000-000000000001", actorId: "00000000-0000-4000-8000-000000000002", name: "企业甲", role: "owner" };
const session = { status: "authenticated", email: "owner@example.test", expiresAt: "2099-01-01T00:00:00Z", contextVersion: "a".repeat(32) + ".1", csrfToken: "c".repeat(64), currentTenant: null };
const json = (value: unknown) => new Response(JSON.stringify(value));

function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><BrowserSessionBoundary>{value => <div><p>工作资料：{value.tenant.name}</p><button onClick={value.chooseTenant}>切换企业</button><button onClick={value.logout}>退出登录</button></div>}</BrowserSessionBoundary></QueryClientProvider>);
  return client;
}

beforeEach(() => { configureAuthentication("browser"); setLocale("zh"); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); configureAuthentication("bearer"); sessionStorage.clear(); });

it("shows sign-in instead of reading a previously stored developer token", async () => {
  sessionStorage.setItem("enterprise-doc.upload-token.v1", "old-developer-token");
  const fetcher = vi.fn<Fetcher>().mockResolvedValue(json({ status: "anonymous" }));
  vi.stubGlobal("fetch", fetcher);
  show();
  expect(await screen.findByRole("link", { name: "使用企业账号登录" })).toHaveAttribute("href", "/auth/login");
  expect(screen.queryByText(/工作资料/)).not.toBeInTheDocument();
  expect(screen.queryByLabelText(/token/i)).not.toBeInTheDocument();
  expect(fetcher.mock.calls.every(([url]) => url === "/auth/session")).toBe(true);
});

it("selects enterprises by name, clears cached evidence when switching and confirms logout", async () => {
  let selected = false;
  vi.stubGlobal("fetch", vi.fn<Fetcher>((url, init) => {
    if (url === "/auth/tenants") return Promise.resolve(json([tenant]));
    if (url === "/auth/tenant") {
      selected = true;
      if (typeof init?.body !== "string") throw new Error("Expected a JSON request body.");
      expect(JSON.parse(init.body) as unknown).toEqual({ tenantId: tenant.tenantId });
      return Promise.resolve(json({ ...session, contextVersion: "a".repeat(32) + ".2", currentTenant: tenant }));
    }
    if (url === "/auth/logout") return Promise.resolve(json({ revoked: true }));
    return Promise.resolve(json({ ...session, currentTenant: selected ? tenant : null }));
  }));
  const client = show();
  fireEvent.click(await screen.findByRole("button", { name: /进入 企业甲/ }));
  expect(await screen.findByText("工作资料：企业甲")).toBeInTheDocument();
  client.setQueryData(["sensitive-old-result"], "旧企业证据");
  fireEvent.click(screen.getByRole("button", { name: "切换企业" }));
  expect(screen.queryByText("工作资料：企业甲")).not.toBeInTheDocument();
  await screen.findByRole("heading", { name: "选择企业" });
  expect(client.getQueryData(["sensitive-old-result"])).toBeUndefined();
  fireEvent.click(screen.getByRole("button", { name: /进入 企业甲/ }));
  await screen.findByText("工作资料：企业甲");
  fireEvent.click(screen.getByRole("button", { name: "退出登录" }));
  await screen.findByRole("link", { name: "使用企业账号登录" });
  expect(screen.queryByText(/工作资料/)).not.toBeInTheDocument();
});

it("submits an admission code with only the chosen company name and removes the code after success", async () => {
  const fetcher = vi.fn<Fetcher>(url => Promise.resolve(url === "/auth/session" ? json(session) : url === "/auth/admission/accept" ? json({ tenantId: tenant.tenantId, replayed: false }) : json([])));
  vi.stubGlobal("fetch", fetcher);
  show();
  await screen.findByRole("heading", { name: "选择企业" });
  fireEvent.click(screen.getByText("开通新企业"));
  fireEvent.change(screen.getByLabelText("开通码"), { target: { value: "adm1_" + "x".repeat(43) } });
  fireEvent.change(screen.getByLabelText("企业名称"), { target: { value: "客户新企业" } });
  fireEvent.click(screen.getByRole("button", { name: "确认开通" }));
  await screen.findByText("企业已开通，请选择企业进入。若列表未更新，可重新核验。");
  const request = fetcher.mock.calls.find(([url]) => url === "/auth/admission/accept")?.[1];
  if (typeof request?.body !== "string") throw new Error("Expected a JSON request body.");
  expect(Object.keys(JSON.parse(request.body) as Record<string, unknown>)).toEqual(["token", "tenantName"]);
  await waitFor(() => expect(screen.getByLabelText("开通码")).toHaveValue(""));
});

it("consumes an admission fragment immediately without preserving it across sign-in", () => {
  const code = "adm1_" + "x".repeat(43);
  window.history.replaceState(null, "", "/#/admission?token=" + code);
  expect(consumeAuthEntry().admissionToken).toBe(code);
  expect(window.location.hash).toBe("");
  expect(consumeAuthEntry().admissionToken).toBeNull();
});

it("shows the configured GitHub sign-in and a verified primary email action", async () => {
  vi.stubGlobal("fetch", vi.fn<Fetcher>().mockResolvedValue(json({ status: "anonymous", loginProvider: "github" })));
  window.history.replaceState(null, "", "/#/signin?error=github_email_required");
  const entry = consumeAuthEntry();
  const client = new QueryClient();
  render(<QueryClientProvider client={client}><BrowserSessionBoundary entry={entry}>{() => <p>工作资料</p>}</BrowserSessionBoundary></QueryClientProvider>);
  expect(await screen.findByRole("link", { name: "使用 GitHub 登录" })).toHaveAttribute("href", "/auth/login");
  expect(screen.getByText("请先在 GitHub 邮箱设置中验证主邮箱，再重新登录。")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "打开 GitHub 邮箱设置" })).toHaveAttribute("href", "https://github.com/settings/emails");
  expect(window.location.hash).toBe("");
});

it("keeps GitHub sign-in after a restored authenticated session logs out", async () => {
  vi.stubGlobal("fetch", vi.fn<Fetcher>(url => Promise.resolve(json(
    url === "/auth/logout" ? { revoked: true } : { ...session, loginProvider: "github", currentTenant: tenant },
  ))));
  show();
  fireEvent.click(await screen.findByRole("button", { name: "退出登录" }));
  expect(await screen.findByRole("link", { name: "使用 GitHub 登录" })).toHaveAttribute("href", "/auth/login");
  expect(screen.queryByText(/工作资料/)).not.toBeInTheDocument();
});

it("enters an isolated demo without OAuth and preserves it on foreground verification", async () => {
  let active = false;
  const guest = { ...session, email: null, demo: true, loginProvider: "github", currentTenant: { ...tenant, name: "演示企业" } };
  const fetcher = vi.fn<Fetcher>(url => {
    if (url === "/auth/demo") { active = true; return Promise.resolve(json(guest)); }
    if (url === "/auth/logout") { active = false; return Promise.resolve(json({ revoked: true })); }
    return Promise.resolve(json(active ? guest : { status: "anonymous", demoAvailable: true, loginProvider: "github" }));
  });
  vi.stubGlobal("fetch", fetcher);
  const client = show();
  fireEvent.click(await screen.findByRole("button", { name: "一键进入演示" }));
  await screen.findByText("工作资料：演示企业");
  client.setQueryData(["demo-draft"], "draft");
  fireEvent(window, new Event("pageshow"));
  await waitFor(() => expect(screen.getByText("工作资料：演示企业")).toBeVisible());
  expect(client.getQueryData(["demo-draft"])).toBe("draft");
  expect(fetcher.mock.calls.some(([url]) => url === "/auth/login" || url === "/auth/tenants")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "退出登录" }));
  await screen.findByRole("link", { name: "使用 GitHub 登录" });
  expect(client.getQueryData(["demo-draft"])).toBeUndefined();
});

it("shows full demo capacity without retrying workspace creation automatically", async () => {
  const fetcher = vi.fn<Fetcher>(url => Promise.resolve(url === "/auth/demo"
    ? new Response(JSON.stringify({ error: { code: "demo_capacity_reached", message: "Full", requestId: null } }), { status: 429 })
    : json({ status: "anonymous", demoAvailable: true, loginProvider: "github" })));
  vi.stubGlobal("fetch", fetcher);
  show();
  fireEvent.click(await screen.findByRole("button", { name: "一键进入演示" }));
  await screen.findByText("演示空间当前已满，请稍后再试。");
  expect(fetcher.mock.calls.filter(([url]) => url === "/auth/demo")).toHaveLength(1);
});
