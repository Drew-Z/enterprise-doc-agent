import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

function renderApp() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>,
  );
}

function healthResponse(status: 200 | 503, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("App readiness dashboard", () => {
  it("renders a stable loading state", () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => new Promise(() => undefined));

    renderApp();

    expect(screen.getByRole("status")).toHaveTextContent("Checking platform readiness");
    expect(screen.getByRole("button", { name: "Refresh readiness" })).toBeDisabled();
  });

  it("renders healthy components from a typed 200 response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      healthResponse(200, {
        status: "ready",
        checks: {
          database: { status: "up" },
          redis: { status: "up" },
          object_store: { status: "up" },
        },
      }),
    );

    renderApp();

    expect(await screen.findByText("Platform ready")).toBeInTheDocument();
    expect(screen.getAllByText("Operational")).toHaveLength(3);
  });

  it("renders degraded state from a typed 503 response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      healthResponse(503, {
        status: "not_ready",
        checks: {
          database: { status: "up" },
          redis: { status: "down" },
          object_store: { status: "timeout" },
        },
      }),
    );

    renderApp();

    expect(await screen.findByText("Platform degraded")).toBeInTheDocument();
    expect(screen.getByText("Unavailable")).toBeInTheDocument();
    expect(screen.getByText("Timed out")).toBeInTheDocument();
  });

  it.each([
    ["network error", () => Promise.reject(new TypeError("connection failed"))],
    [
      "invalid schema",
      () => Promise.resolve(healthResponse(200, { status: "ready", checks: {} })),
    ],
  ])("renders unreachable state for %s", async (_name, responseFactory) => {
    vi.spyOn(globalThis, "fetch").mockImplementation(responseFactory);

    renderApp();

    expect(await screen.findByText("API unreachable")).toBeInTheDocument();
  });

  it("manually refreshes after a failure", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockRejectedValueOnce(new TypeError("connection failed"))
      .mockResolvedValueOnce(
        healthResponse(200, {
          status: "ready",
          checks: {
            database: { status: "up" },
            redis: { status: "up" },
            object_store: { status: "up" },
          },
        }),
      );

    renderApp();
    await screen.findByText("API unreachable");

    fireEvent.click(screen.getByRole("button", { name: "Refresh readiness" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("Platform ready")).toBeInTheDocument();
  });
});
