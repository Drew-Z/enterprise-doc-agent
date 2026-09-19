import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { setLocale } from "../i18n";
import { BrowserSessionBoundary } from "./BrowserSessionBoundary";
import { consumeAuthEntry } from "./entry";
import { configureAuthentication, type Fetcher } from "./transport";

const tenant = { tenantId: "00000000-0000-4000-8000-000000000001", actorId: "00000000-0000-4000-8000-000000000002", name: "已有企业", role: "owner" };
const target = { ...tenant, tenantId: "00000000-0000-4000-8000-000000000003", name: "受邀企业", role: "member" };
const session = { status: "authenticated", email: "member@example.test", expiresAt: "2099-01-01T00:00:00Z", contextVersion: "a".repeat(32) + ".1", csrfToken: "c".repeat(64), currentTenant: tenant };
const token = "inv1_" + "x".repeat(43);
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });

beforeEach(() => { configureAuthentication("browser"); setLocale("zh"); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); configureAuthentication("bearer"); window.history.replaceState(null, "", "/"); });

it("opens an invitation ahead of an existing workspace and requires confirmation plus explicit selection", async () => {
  let accepted = false;
  const fetcher = vi.fn<Fetcher>(url => {
    if (url === "/auth/session") return Promise.resolve(json(session));
    if (url === "/auth/tenants") return Promise.resolve(json(accepted ? [tenant, target] : [tenant]));
    if (url === "/auth/invitations/inspect") return Promise.resolve(json({ tenantName: target.name, expiresAt: session.expiresAt, state: "pending" }));
    if (url === "/auth/invitations/accept") {
      accepted = true;
      return Promise.resolve(json({ tenantId: target.tenantId, tenantName: target.name, membershipId: "00000000-0000-4000-8000-000000000004", replayed: false }));
    }
    if (url === "/auth/tenant") return Promise.resolve(json({ ...session, contextVersion: "a".repeat(32) + ".2", currentTenant: target }));
    throw new Error("Unexpected HTTP path");
  });
  vi.stubGlobal("fetch", fetcher);
  window.history.replaceState(null, "", "/#/invitation?token=" + token);
  const entry = consumeAuthEntry();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<StrictMode><QueryClientProvider client={client}><BrowserSessionBoundary entry={entry}>{value => <p>工作资料：{value.tenant.name}</p>}</BrowserSessionBoundary></QueryClientProvider></StrictMode>);
  fireEvent.click(await screen.findByRole("button", { name: "查看邀请" }));
  expect(entry.invitationToken).toBeNull();
  expect(window.location.hash).toBe("");
  expect(await screen.findByRole("heading", { name: "受邀企业" })).toBeInTheDocument();
  expect(screen.queryByText(/工作资料/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "确认加入企业" }));
  await screen.findByText("已加入企业，请选择企业进入。若列表未更新，可重新核验。");
  expect(screen.queryByText(/工作资料/)).not.toBeInTheDocument();
  expect(screen.queryByLabelText("邀请代码")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "进入 受邀企业" }));
  expect(await screen.findByText("工作资料：受邀企业")).toBeInTheDocument();
  const writes = fetcher.mock.calls.filter(([url]) => url === "/auth/invitations/accept");
  expect(writes).toHaveLength(1);
  expect(writes[0]?.[1]?.body === JSON.stringify({ token })).toBe(true);
  expect(Array.from({ length: sessionStorage.length }, (_, index) => sessionStorage.getItem(sessionStorage.key(index) ?? "")).some(value => value?.includes(token))).toBe(false);
});

it("discards an invitation arriving on an already anonymous page and asks to reopen it after sign-in", async () => {
  let signedIn = false;
  vi.stubGlobal("fetch", vi.fn<Fetcher>(url => Promise.resolve(json(url === "/auth/session" ? signedIn ? { ...session, currentTenant: null } : { status: "anonymous" } : []))));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><BrowserSessionBoundary>{() => <p>工作资料</p>}</BrowserSessionBoundary></QueryClientProvider>);
  await screen.findByRole("heading", { name: "登录工作台" });
  act(() => {
    window.history.replaceState(null, "", "/#/invitation?token=" + token);
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });
  expect(await screen.findByText("请先登录，再重新打开原邀请链接。")).toBeInTheDocument();
  expect(window.location.hash).toBe("");
  act(() => { signedIn = true; window.dispatchEvent(new Event("pageshow")); });
  fireEvent.click(await screen.findByRole("button", { name: "加入已有企业" }));
  expect(screen.getByLabelText("邀请代码")).toHaveValue("");
});

it("returns to invitation review after a membership conflict without retrying acceptance", async () => {
  const fetcher = vi.fn<Fetcher>(url => {
    if (url === "/auth/session") return Promise.resolve(json({ ...session, currentTenant: null }));
    if (url === "/auth/tenants") return Promise.resolve(json([]));
    if (url === "/auth/invitations/inspect") return Promise.resolve(json({ tenantName: target.name, expiresAt: session.expiresAt, state: "pending" }));
    return Promise.resolve(json({ error: { code: "invitation_conflict", message: "Do not render server detail", requestId: "test" } }, 409));
  });
  vi.stubGlobal("fetch", fetcher);
  window.history.replaceState(null, "", "/#/invitation?token=" + token);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><BrowserSessionBoundary entry={consumeAuthEntry()}>{() => <p>工作资料</p>}</BrowserSessionBoundary></QueryClientProvider>);
  fireEvent.click(await screen.findByRole("button", { name: "查看邀请" }));
  fireEvent.click(await screen.findByRole("button", { name: "确认加入企业" }));
  expect(await screen.findByRole("button", { name: "查看邀请" })).toBeEnabled();
  expect(screen.getByText("邀请状态已变化或正在处理，请重新查看邀请后再试。")).toBeInTheDocument();
  expect(screen.queryByText("Do not render server detail")).not.toBeInTheDocument();
  expect(fetcher.mock.calls.filter(([url]) => url === "/auth/invitations/accept")).toHaveLength(1);
  expect(screen.getByLabelText<HTMLInputElement>("邀请代码").value === token).toBe(true);
});
