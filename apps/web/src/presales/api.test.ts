import { afterEach, describe, expect, it, vi } from "vitest";
import { presalesApi } from "./api";

const packetId = "10000000-0000-4000-8000-000000000001";
const rowId = "10000000-0000-4000-8000-000000000002";
const otherId = "10000000-0000-4000-8000-000000000003";
const attemptId = "10000000-0000-4000-8000-000000000004";
const admitted = { rowId, disposition: "enqueued", attemptId };
const valid = { packetId, admissions: [admitted], rejected: [] };

afterEach(() => vi.restoreAllMocks());

describe("Presales durable receipt HTTP contract", () => {
  it.each(["enqueued", "replayed", "already_drafted"])("accepts %s without interpreting it as a draft", async disposition => {
    const receipt = { ...valid, admissions: [{ ...admitted, disposition, attemptId: disposition === "already_drafted" ? null : attemptId }] };
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(receipt), { status: disposition === "enqueued" ? 202 : 200 }));
    const result = await presalesApi("test-token").admit(packetId, rowId, "once", new AbortController().signal);
    expect(result).toEqual(receipt);
    expect(fetch).toHaveBeenCalledOnce();
    expect(fetch.mock.calls[0][0]).toBe(`/api/presales/${packetId}/rows/${rowId}/generate?response=receipt`);
    expect(new Headers(fetch.mock.calls[0][1]?.headers).get("Idempotency-Key")).toBe("once");
  });

  it.each([
    { ...valid, packetId: otherId },
    { ...valid, admissions: [] },
    { ...valid, admissions: [{ ...admitted, rowId: otherId }] },
    { ...valid, admissions: [admitted, admitted] },
    { ...valid, admissions: [{ ...admitted, attemptId: null }] },
    { ...valid, admissions: [{ ...admitted, disposition: "already_drafted" }] },
    { ...valid, admissions: [{ ...admitted, disposition: "succeeded" }] },
    { ...valid, rejected: [{ rowId, code: "presales_usage_limit" }] },
    { ...valid, sources: [{ text: "Unexpected evidence" }] },
  ])("rejects mismatched or malformed receipts at the boundary", async body => {
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(body), { status: 202 }));
    await expect(presalesApi("test-token").admit(packetId, rowId, "once", new AbortController().signal)).rejects.toThrow();
    expect(fetch).toHaveBeenCalledOnce();
  });

  it("covers each requested batch row exactly once across admission and rejection", async () => {
    const receipt = { ...valid, rejected: [{ rowId: otherId, code: "presales_usage_limit" }] };
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(receipt), { status: 202 }));
    expect(await presalesApi("test-token").admitBatch(packetId, [rowId, otherId], "batch-once", new AbortController().signal)).toEqual(receipt);
    expect(fetch.mock.calls[0][1]?.body).toBe(JSON.stringify({ rowIds: [rowId, otherId] }));
  });
});
