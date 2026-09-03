import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchReadiness, isReadinessResponse } from "./health";

const readyPayload = {
  status: "ready",
  checks: {
    database: { status: "up" },
    redis: { status: "up" },
    object_store: { status: "up" },
  },
  checked_at: "2026-08-27T10:15:00+00:00",
};

afterEach(() => vi.restoreAllMocks());

describe("readiness contract", () => {
  it("accepts a server probe timestamp and rejects an invalid one", () => {
    expect(isReadinessResponse(readyPayload)).toBe(true);
    expect(isReadinessResponse({ ...readyPayload, checked_at: "not-a-date" })).toBe(false);
  });

  it("normalizes the server timestamp for the web model", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(readyPayload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(fetchReadiness()).resolves.toMatchObject({
      status: "ready",
      checkedAt: "2026-08-27T10:15:00+00:00",
    });
  });
});
