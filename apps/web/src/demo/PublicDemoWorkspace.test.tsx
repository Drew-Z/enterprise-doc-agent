import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../App";
import { activateBrowserCredential, configureAuthentication, retireBrowserCredential, type Fetcher } from "../auth/transport";
import { setLocale } from "../i18n";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); retireBrowserCredential(); configureAuthentication("bearer"); window.history.replaceState(null, "", "/"); });

it("offers real source uploads and presales with samples and no admin navigation", async () => {
  configureAuthentication("browser"); setLocale("zh");
  const tenant = { tenantId: "00000000-0000-4000-8000-000000000001", actorId: "00000000-0000-4000-8000-000000000002", role: "owner" as const, name: "演示企业" };
  activateBrowserCredential({ tenantId: tenant.tenantId, actorId: tenant.actorId, contextVersion: "a".repeat(32) + ".1", csrfToken: "c".repeat(64) });
  vi.stubGlobal("fetch", vi.fn<Fetcher>(url => Promise.resolve(new Response(JSON.stringify(url === "/api/demo" ? { attemptsUsed: 2, attemptLimit: 6, uploadLimit: 6, maxFileBytes: 2097152, storageBytes: 10485760 } : [])))));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><App browserSession={{ tenant, email: "演示访客", demo: { expiresAt: "2099-01-01T00:00:00Z" }, chooseTenant: vi.fn(), logout: vi.fn() }} /></QueryClientProvider>);
  await screen.findByText("已尝试 2 / 6 次生成");
  expect(screen.getByText("当前企业：演示企业 · 演示访客")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "产品说明" })).toHaveAttribute("download");
  expect(screen.getByRole("link", { name: "产品说明" })).toHaveAttribute("href", "/demo/product-guide.txt");
  expect(screen.getByRole("link", { name: "下载问卷" })).toHaveAttribute("href", "/demo/requirements.txt");
  expect(screen.queryByRole("button", { name: "切换企业" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "身份与访问" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "2. 生成与复核回应" }));
  expect(await screen.findByText(/每行一条，最多 6 条/)).toBeInTheDocument();
});
