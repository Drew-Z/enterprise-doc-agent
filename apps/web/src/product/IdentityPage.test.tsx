import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createUploadTokenStore } from "../upload/persistence";
import { IdentityPage } from "./IdentityPage";

const binding = {
  bindingId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  tenantId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  issuer: "https://idp.example.com",
  subject: "subject-123",
  userId: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
  userEmail: "member@example.com",
  isActive: true,
  createdAt: "2026-08-26T00:00:00+00:00",
  updatedAt: "2026-08-26T00:00:00+00:00",
};

function requestPath(input: RequestInfo | URL): string {
  return typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
}

beforeEach(() => {
  sessionStorage.clear();
  createUploadTokenStore(sessionStorage).save("local-token");
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("IdentityPage", () => {
  it("searches members and manages the binding lifecycle", async () => {
    const inactiveBinding = {
      ...binding,
      bindingId: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
      userEmail: "inactive.member@example.com",
      isActive: false,
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      if (requestPath(input).includes("/identity-bindings/members")) return Promise.resolve(new Response(JSON.stringify([{ userId: binding.userId, email: binding.userEmail, role: "member" }]), { status: 200 }));
      if (requestPath(input).includes("/api/members")) return Promise.resolve(new Response(JSON.stringify([]), { status: 200 }));
      if ((init?.method ?? "GET") === "POST") return Promise.resolve(new Response(JSON.stringify(binding), { status: 200 }));
      if ((init?.method ?? "GET") === "DELETE") return Promise.resolve(new Response(JSON.stringify({ ...binding, isActive: false }), { status: 200 }));
      return Promise.resolve(new Response(JSON.stringify([binding, inactiveBinding]), { status: 200 }));
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    render(<QueryClientProvider client={queryClient}><IdentityPage canManage /></QueryClientProvider>);

    expect(await screen.findByRole("combobox", { name: "Active tenant member" })).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "Issuer URL" }), { target: { value: binding.issuer } });
    fireEvent.change(screen.getByRole("textbox", { name: "Subject" }), { target: { value: binding.subject } });
    fireEvent.change(screen.getByRole("combobox", { name: "Active tenant member" }), { target: { value: binding.userId } });
    fireEvent.click(screen.getByRole("button", { name: "Create binding" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "Deactivate" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "Activate" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => init?.method === "POST" && requestPath(input).endsWith("/activate"))).toBe(true));
  });

  it("keeps showcase identity bindings read-only and makes no API calls", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    render(<QueryClientProvider client={queryClient}><IdentityPage showcaseMode canManage /></QueryClientProvider>);
    expect(await screen.findByText("former.member@example.com")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create binding" })).toBeDisabled();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
