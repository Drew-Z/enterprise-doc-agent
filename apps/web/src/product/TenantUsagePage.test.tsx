import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { setLocale } from "../i18n";
import { tenantUsage, usageActor, usageTenantA, usageTenantB, usageWithoutPeriod } from "../test/tenantUsage";
import { TenantUsagePage, type TenantUsagePageProps } from "./TenantUsagePage";

function response(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status }); }
const defaults = {
  credential: "token-a", tenantId: usageTenantA, contextKey: `${usageTenantA}:${usageActor}:1`,
  canView: true, navigate: vi.fn(),
} satisfies TenantUsagePageProps;

function mount(props: Partial<TenantUsagePageProps> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const view = render(<QueryClientProvider client={client}><TenantUsagePage {...defaults} {...props} /></QueryClientProvider>);
  return {
    ...view, client,
    change: (next: Partial<TenantUsagePageProps>) => view.rerender(<QueryClientProvider client={client}><TenantUsagePage {...defaults} {...next} /></QueryClientProvider>),
  };
}

beforeEach(() => setLocale("en"));
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("shows current capacity, UTC dates, actual resources and unknown model cost", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(response(tenantUsage()));
  mount();
  const generation = await screen.findByRole("region", { name: "Presales generation capacity" });
  expect(within(generation).getByText("24")).toBeInTheDocument();
  expect(within(generation).getByText("6")).toBeInTheDocument();
  expect(within(generation).getByText("70")).toBeInTheDocument();
  expect(within(generation).getByText("100")).toBeInTheDocument();
  expect(screen.getByText("team-pilot")).toBeInTheDocument();
  expect(within(generation).getByText(/UTC/)).toBeInTheDocument();
  const storage = screen.getByRole("region", { name: "Storage" });
  const agent = screen.getByRole("region", { name: "Agent tasks" });
  expect(within(agent).getByText("15")).toBeInTheDocument();
  expect(within(agent).getByText("2")).toBeInTheDocument();
  const processing = screen.getByRole("region", { name: "Document processing" });
  expect(within(processing).getByText("7 MiB")).toBeInTheDocument();
  expect(within(processing).getByText(/separate from storage/)).toBeInTheDocument();
  const calls = screen.getByRole("region", { name: "Agent and embedding calls" });
  expect(within(calls).getByText("128")).toBeInTheDocument();
  expect(within(calls).getByText(/no monetary total/)).toBeInTheDocument();
  expect(within(storage).getByText("256 MiB")).toBeInTheDocument();
  expect(within(storage).getByText("128 MiB")).toBeInTheDocument();
  expect(within(storage).getByText("640 MiB")).toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "Member seats" })).getByText("3")).toBeInTheDocument();
  expect(screen.getByText("Model cost unknown")).toBeInTheDocument();
  expect(screen.getByText("Up to 20 presales events from the current period.")).toBeInTheDocument();
  expect(screen.queryByText(/\$0/)).not.toBeInTheDocument();
});

it.each(["legacy", "inactive"] as const)("explains %s without displaying placeholder zeros as past usage", async status => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(response(usageWithoutPeriod(status)));
  mount();
  expect(await screen.findByText(status === "legacy" ? "No generation period configured" : "No active generation period")).toBeInTheDocument();
  const generation = screen.getByRole("region", { name: "Presales generation capacity" });
  expect(within(generation).queryByText("0")).not.toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Storage" })).toBeInTheDocument();
  expect(screen.queryByRole("list", { name: "Recent usage activity" })).not.toBeInTheDocument();
});

it("does not confuse reserved capacity with successful consumption", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(response(tenantUsage({ providerRequestsUsed: 0, providerRequestsReserved: 100, providerRequestsRemaining: 0 })));
  mount();
  expect(await screen.findByText("No generation capacity available")).toBeInTheDocument();
  expect(screen.getByText(/In-progress generations reserve capacity/)).toBeInTheDocument();
});

it("shows individual cost estimates without claiming a full-period bill", async () => {
  const value = tenantUsage({ costStatus: "known" });
  value.recentEvents[0].estimatedCost = "0.01234567";
  value.recentEvents[0].currency = "USD";
  vi.spyOn(globalThis, "fetch").mockResolvedValue(response(value));
  mount();
  expect(await screen.findByText("0.01234567 USD")).toBeInTheDocument();
  expect(screen.getByText("Some cost estimates available")).toBeInTheDocument();
  expect(screen.getByText(/not a bill or a complete period total/)).toBeInTheDocument();
});

it.each([
  { canView: false }, { credential: null }, { showcaseMode: true }, { sessionPending: true },
])("does not request usage without a live owner session: %j", async props => {
  const fetcher = vi.spyOn(globalThis, "fetch");
  mount(props);
  await act(async () => {});
  expect(fetcher).not.toHaveBeenCalled();
  expect(screen.queryByRole("region", { name: "Presales generation capacity" })).not.toBeInTheDocument();
});

it("hides previous details after a forbidden refresh and allows explicit recovery", async () => {
  const fetcher = vi.spyOn(globalThis, "fetch").mockResolvedValue(response(tenantUsage()));
  mount();
  await screen.findByText("team-pilot");
  fetcher.mockResolvedValue(response({ error: { code: "tenant_usage_forbidden", message: "Owner access required.", requestId: "req-revoked" } }, 403));
  fireEvent.click(screen.getByRole("button", { name: "Refresh usage" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("req-revoked");
  expect(screen.queryByText("team-pilot")).not.toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Storage" })).not.toBeInTheDocument();
  fetcher.mockResolvedValue(response(tenantUsage({ planCode: "restored" })));
  fireEvent.click(screen.getByRole("button", { name: "Refresh usage" }));
  expect(await screen.findByText("restored")).toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledTimes(3);
});

it("reports a service failure with requestId and recovers on refresh", async () => {
  const fetcher = vi.spyOn(globalThis, "fetch").mockResolvedValue(response({ error: { code: "usage_store_unavailable", message: "Unavailable", requestId: "req-outage" } }, 503));
  mount();
  expect(await screen.findByRole("alert")).toHaveTextContent("req-outage");
  expect(fetcher).toHaveBeenCalledTimes(1);
  fetcher.mockResolvedValue(response(tenantUsage()));
  fireEvent.click(screen.getByRole("button", { name: "Refresh usage" }));
  expect(await screen.findByText("team-pilot")).toBeInTheDocument();
});

it("isolates a different enterprise while an old response is still in flight", async () => {
  let finish!: (value: Response) => void;
  const delayed = new Promise<Response>(resolve => { finish = resolve; });
  const fetcher = vi.spyOn(globalThis, "fetch")
    .mockReturnValueOnce(delayed)
    .mockResolvedValue(response(tenantUsage({ tenantId: usageTenantB, planCode: "tenant-b-plan" })));
  const view = mount();
  expect(screen.getByRole("status")).toHaveTextContent("Loading usage");
  await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
  const oldSignal = fetcher.mock.calls[0][1]?.signal;
  view.change({ tenantId: usageTenantB, credential: "token-b", contextKey: `${usageTenantB}:${usageActor}:2` });
  expect(await screen.findByText("tenant-b-plan")).toBeInTheDocument();
  await act(async () => { finish(response(tenantUsage({ planCode: "tenant-a-late" }))); await delayed; });
  expect(oldSignal?.aborted).toBe(true);
  expect(screen.queryByText("tenant-a-late")).not.toBeInTheDocument();
  expect(screen.getByText("tenant-b-plan")).toBeInTheDocument();
});

it("unmounts and clears usage when owner permission is removed", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(response(tenantUsage()));
  const view = mount();
  await screen.findByText("team-pilot");
  view.change({ canView: false });
  expect(screen.getByRole("alert")).toHaveTextContent("Only enterprise administrators");
  expect(screen.queryByText("team-pilot")).not.toBeInTheDocument();
  await waitFor(() => expect(view.client.getQueryCache().getAll()).toHaveLength(0));
});
