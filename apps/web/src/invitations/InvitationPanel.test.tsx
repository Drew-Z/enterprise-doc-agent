import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { z } from "zod";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { setLocale } from "../i18n";
import { activateBrowserCredential, configureAuthentication, retireBrowserCredential, type Fetcher } from "../auth/transport";
import { InvitationPanel } from "./InvitationPanel";

const item = { invitationId: "00000000-0000-4000-8000-000000000001", email: "colleague@example.test", state: "pending", generation: 1, expiresAt: "2099-01-01T00:00:00Z", createdAt: "2026-09-14T00:00:00Z", updatedAt: "2026-09-14T00:00:00Z" };
const token = "inv1_" + "x".repeat(43);
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
const copy = vi.fn().mockResolvedValue(undefined);
const clipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, "clipboard");

function requestBody(init?: RequestInit) {
  if (typeof init?.body !== "string") throw new Error("Expected a JSON request body");
  const body: unknown = JSON.parse(init.body);
  return z.record(z.string(), z.unknown()).parse(body);
}

beforeEach(() => {
  configureAuthentication("browser"); setLocale("zh"); copy.mockReset().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: copy } });
});
afterEach(() => {
  cleanup(); vi.unstubAllGlobals(); configureAuthentication("bearer");
  if (clipboardDescriptor) Object.defineProperty(navigator, "clipboard", clipboardDescriptor);
  else Reflect.deleteProperty(navigator, "clipboard");
});

function show(canManage = true) {
  const credential = activateBrowserCredential({
    contextVersion: "a".repeat(32) + ".1", csrfToken: "c".repeat(64),
    tenantId: "00000000-0000-4000-8000-000000000002", actorId: "00000000-0000-4000-8000-000000000003",
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const view = render(<StrictMode><QueryClientProvider client={client}><InvitationPanel credential={credential} canManage={canManage} /></QueryClientProvider></StrictMode>);
  return { client, credential, ...view };
}

it("creates and copies a link once without putting its secret in the DOM or query cache", async () => {
  let created = false;
  const fetcher = vi.fn<Fetcher>((url, init) => {
    if (url !== "/api/invitations") throw new Error("Unexpected path");
    if (init?.method === "POST") { created = true; return Promise.resolve(json({ invitation: item, token, replayed: false })); }
    return Promise.resolve(json({ items: created ? [item] : [], seats: { active: 1, limit: 3, remaining: 2 }, eligible: true, hasMore: false }));
  });
  vi.stubGlobal("fetch", fetcher);
  const { client } = show();
  fireEvent.change(await screen.findByLabelText("受邀邮箱"), { target: { value: item.email } });
  fireEvent.click(screen.getByRole("button", { name: "生成邀请链接" }));
  fireEvent.click(await screen.findByRole("button", { name: "复制邀请链接" }));
  await waitFor(() => expect(copy).toHaveBeenCalledTimes(1));
  expect(copy.mock.calls[0]?.[0] === window.location.origin + "/#/invitation?token=" + token).toBe(true);
  expect(document.body.textContent?.includes(token)).toBe(false);
  expect(JSON.stringify(client.getQueryCache().getAll().map(query => query.state.data)).includes(token)).toBe(false);
  expect(client.getMutationCache().getAll()).toHaveLength(0);
  expect(screen.getByText(/尚未发送邮件/)).toBeInTheDocument();
  const write = fetcher.mock.calls.find(([, init]) => init?.method === "POST")?.[1];
  expect(new Headers(write?.headers).get("X-Session-Context")).toBe("a".repeat(32) + ".1");
  const body = requestBody(write);
  expect(body.email).toBe(item.email);
  expect(body.operationId).toMatch(/^[0-9a-f-]{36}$/);
});

it("removes a generated link immediately when its captured session is retired", async () => {
  vi.stubGlobal("fetch", vi.fn<Fetcher>((_url, init) => Promise.resolve(json(init?.method === "POST"
    ? { invitation: item, token, replayed: false }
    : { items: [], seats: { active: 1, limit: 3, remaining: 2 }, eligible: true, hasMore: false }))));
  show();
  fireEvent.change(await screen.findByLabelText("受邀邮箱"), { target: { value: item.email } });
  fireEvent.click(screen.getByRole("button", { name: "生成邀请链接" }));
  await screen.findByRole("button", { name: "复制邀请链接" });
  act(() => retireBrowserCredential());
  expect(screen.queryByRole("button", { name: "复制邀请链接" })).not.toBeInTheDocument();
  expect(copy).not.toHaveBeenCalled();
});

it("regenerates and revokes the current invitation generation without retaining a revoked link", async () => {
  const nextToken = "inv1_" + "y".repeat(43);
  const next = { ...item, generation: 2 };
  const fetcher = vi.fn<Fetcher>((url, init) => {
    if (url === `/api/invitations/${item.invitationId}/regenerate`) return Promise.resolve(json({ invitation: next, token: nextToken, replayed: false }));
    if (url === `/api/invitations/${item.invitationId}/revoke`) return Promise.resolve(json({ invitation: { ...next, state: "revoked" }, token: null, replayed: false }));
    if (url === "/api/invitations" && !init?.method) return Promise.resolve(json({ items: [item], seats: { active: 2, limit: 3, remaining: 1 }, eligible: true, hasMore: false }));
    throw new Error("Unexpected path");
  });
  vi.stubGlobal("fetch", fetcher);
  const { client } = show();
  fireEvent.click(await screen.findByRole("button", { name: "重新生成链接" }));
  fireEvent.click(await screen.findByRole("button", { name: "复制邀请链接" }));
  await waitFor(() => expect(copy).toHaveBeenCalledTimes(1));
  expect(copy.mock.calls[0]?.[0] === window.location.origin + "/#/invitation?token=" + nextToken).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "撤销邀请" }));
  expect(await screen.findByText("已撤销")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "复制邀请链接" })).not.toBeInTheDocument();
  const writes = fetcher.mock.calls.filter(([, init]) => init?.method === "POST");
  expect(writes).toHaveLength(2);
  const bodies = writes.map(([, init]) => requestBody(init));
  expect(bodies.map(body => body.expectedGeneration)).toEqual([1, 2]);
  expect(new Set(bodies.map(body => body.operationId)).size).toBe(2);
  expect(JSON.stringify(client.getQueryCache().getAll().map(query => query.state.data)).includes(nextToken)).toBe(false);
  expect(client.getMutationCache().getAll()).toHaveLength(0);
});

it("does not retry an unconfirmed write and requires a refresh before explicit regeneration", async () => {
  let created = false;
  const fetcher = vi.fn<Fetcher>((url, init) => {
    if (url === "/api/invitations" && init?.method === "POST") { created = true; return Promise.reject(new TypeError("offline")); }
    if (url === `/api/invitations/${item.invitationId}/regenerate`) return Promise.resolve(json({ invitation: { ...item, generation: 2 }, token, replayed: false }));
    return Promise.resolve(json({ items: created ? [item] : [], seats: { active: 1, limit: 3, remaining: 2 }, eligible: true, hasMore: false }));
  });
  vi.stubGlobal("fetch", fetcher);
  show();
  fireEvent.change(await screen.findByLabelText("受邀邮箱"), { target: { value: item.email } });
  fireEvent.click(screen.getByRole("button", { name: "生成邀请链接" }));
  await screen.findByText(/操作结果尚未确认/);
  expect(screen.getByRole("button", { name: "生成邀请链接" })).toBeDisabled();
  expect(fetcher.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "刷新邀请" }));
  const regenerate = await screen.findByRole("button", { name: "重新生成链接" });
  await waitFor(() => expect(regenerate).toBeEnabled());
  expect(screen.queryByRole("button", { name: "复制邀请链接" })).not.toBeInTheDocument();
  fireEvent.click(regenerate);
  await screen.findByRole("button", { name: "复制邀请链接" });
  expect(fetcher.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(2);
});

it("retries a failed clipboard copy without repeating the invitation write and clears the link on refresh", async () => {
  copy.mockRejectedValueOnce(new DOMException("Denied", "NotAllowedError"));
  const fetcher = vi.fn<Fetcher>((_url, init) => Promise.resolve(json(init?.method === "POST"
    ? { invitation: item, token, replayed: false }
    : { items: [item], seats: { active: 3, limit: 3, remaining: 0 }, eligible: true, hasMore: false })));
  vi.stubGlobal("fetch", fetcher);
  show();
  fireEvent.change(await screen.findByLabelText("受邀邮箱"), { target: { value: item.email } });
  fireEvent.click(screen.getByRole("button", { name: "生成邀请链接" }));
  fireEvent.click(await screen.findByRole("button", { name: "复制邀请链接" }));
  await screen.findByText(/复制失败/);
  fireEvent.click(screen.getByRole("button", { name: "复制邀请链接" }));
  await screen.findByText(/邀请链接已复制/);
  expect(copy).toHaveBeenCalledTimes(2);
  expect(fetcher.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
  expect(screen.getByText("3 / 3")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "刷新邀请" }));
  expect(screen.queryByRole("button", { name: "复制邀请链接" })).not.toBeInTheDocument();
});

it.each(["retire", "unmount"] as const)("discards a secret response body that arrives after %s", async action => {
  let deliver: (() => void) | undefined;
  const fetcher = vi.fn<Fetcher>((_url, init) => {
    if (init?.method === "POST") return Promise.resolve(new Response(new ReadableStream({
      start(controller) { deliver = () => { controller.enqueue(new TextEncoder().encode(JSON.stringify({ invitation: item, token, replayed: false }))); controller.close(); }; },
    })));
    return Promise.resolve(json({ items: [], seats: { active: 1, limit: 3, remaining: 2 }, eligible: true, hasMore: false }));
  });
  vi.stubGlobal("fetch", fetcher);
  const { client, unmount } = show();
  fireEvent.change(await screen.findByLabelText("受邀邮箱"), { target: { value: item.email } });
  fireEvent.click(screen.getByRole("button", { name: "生成邀请链接" }));
  await waitFor(() => expect(deliver).toBeTypeOf("function"));
  await act(async () => {
    if (action === "retire") retireBrowserCredential(); else unmount();
    deliver?.();
    await new Promise(resolve => setTimeout(resolve, 0));
  });
  expect(screen.queryByRole("button", { name: "复制邀请链接" })).not.toBeInTheDocument();
  expect(JSON.stringify(client.getQueryCache().getAll().map(query => query.state.data)).includes(token)).toBe(false);
  expect(client.getMutationCache().getAll()).toHaveLength(0);
  expect(copy).not.toHaveBeenCalled();
});

it("does not request private invitation data for a member", () => {
  const fetcher = vi.fn<Fetcher>(); vi.stubGlobal("fetch", fetcher);
  show(false);
  expect(screen.getByText("仅企业所有者可以管理邀请。")).toBeInTheDocument();
  expect(fetcher).not.toHaveBeenCalled();
  expect(screen.queryByLabelText("受邀邮箱")).not.toBeInTheDocument();
});

it("shows an unconfigured entitlement without presenting unlimited seats or a create form", async () => {
  vi.stubGlobal("fetch", vi.fn<Fetcher>(() => Promise.resolve(json({ items: [], seats: { active: 7, limit: null, remaining: null }, eligible: false, hasMore: false }))));
  show();
  expect(await screen.findByText("7 / 未配置")).toBeInTheDocument();
  expect(screen.queryByLabelText("受邀邮箱")).not.toBeInTheDocument();
});

it("maps the server unavailable code to an actionable notice", async () => {
  vi.stubGlobal("fetch", vi.fn<Fetcher>((_url, init) => Promise.resolve(init?.method === "POST"
    ? json({ error: { code: "invitations_unavailable", message: "Do not render server detail", requestId: "request-1" } }, 503)
    : json({ items: [], seats: { active: 1, limit: 3, remaining: 2 }, eligible: true, hasMore: false }))));
  show();
  fireEvent.change(await screen.findByLabelText("受邀邮箱"), { target: { value: item.email } });
  fireEvent.click(screen.getByRole("button", { name: "生成邀请链接" }));
  expect(await screen.findByText("当前企业暂未开通邀请，请联系管理员。")).toBeInTheDocument();
  expect(screen.queryByText(/操作结果尚未确认/)).not.toBeInTheDocument();
});
