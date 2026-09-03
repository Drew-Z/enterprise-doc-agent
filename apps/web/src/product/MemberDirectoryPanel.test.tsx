import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createUploadTokenStore } from "../upload/persistence";
import { MemberDirectoryPanel } from "./MemberDirectoryPanel";

const owner = { membershipId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", tenantId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", userId: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", email: "owner@example.com", role: "owner", isActive: true, createdAt: "2026-08-26T00:00:00+00:00", updatedAt: "2026-08-26T00:00:00+00:00" };
const member = { ...owner, membershipId: "dddddddd-dddd-4ddd-8ddd-dddddddddddd", userId: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", email: "member@example.com", role: "member" };
const inactive = { ...member, membershipId: "ffffffff-ffff-4fff-8fff-ffffffffffff", userId: "11111111-1111-4111-8111-111111111111", email: "former@example.com", isActive: false };

function requestPath(input: RequestInfo | URL): string {
  return typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
}

beforeEach(() => { sessionStorage.clear(); createUploadTokenStore(sessionStorage).save("token"); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("MemberDirectoryPanel", () => {
  it("provisions, changes roles, deactivates, and reactivates members", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((_input, init) => {
      if ((init?.method ?? "GET") === "GET") return Promise.resolve(new Response(JSON.stringify([owner, member, inactive]), { status: 200 }));
      return Promise.resolve(new Response(JSON.stringify(member), { status: 200 }));
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    render(<QueryClientProvider client={queryClient}><MemberDirectoryPanel canManage currentActorId={owner.userId} /></QueryClientProvider>);

    expect(await screen.findByText("member@example.com")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Change to member" })[0]).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: "Work email" }), { target: { value: "new@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Provision member" }));
    fireEvent.click(screen.getByRole("button", { name: "Promote to owner" }));
    fireEvent.click(screen.getAllByRole("button", { name: "Deactivate member" })[1]);
    fireEvent.click(screen.getByRole("button", { name: "Reactivate member" }));

    await waitFor(() => expect(fetchMock.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(true));
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(true);
    expect(fetchMock.mock.calls.some(([input]) => requestPath(input).endsWith("/activate"))).toBe(true);
  });

  it("keeps showcase directory read-only without API calls", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    render(<QueryClientProvider client={queryClient}><MemberDirectoryPanel showcaseMode canManage currentActorId={owner.userId} /></QueryClientProvider>);
    expect(await screen.findByText("reviewer@example.com")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Provision member" })).toBeDisabled();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
