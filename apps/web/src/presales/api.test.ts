import { afterEach, describe, expect, it, vi } from "vitest";
import { presalesApi, reviewInputSchema } from "./api";

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

const reviewedText = { status: "conditional", answer: "人工核查。", conditions: ["需确认验收。"], missingInformation: [], prerequisites: [{ condition: "需确认验收。", state: "unknown", citationIndexes: [0] }] };
const savedReview = { ...reviewedText, revision: 2, note: "排除无依据条目。", actorId: otherId, reviewedAt: "2026-10-08T00:00:00Z", prerequisiteChanges: { origins: [0], excludedIndexes: [1] } };

it("accepts independent information gaps with the same character budget as the server", async () => {
  const gaps = Array.from({ length: 14 }, (_, index) => `确认第${index}项。`);
  const body = { ...reviewedText, missingInformation: gaps, expectedRevision: 1, note: "" };
  expect(reviewInputSchema.parse(body).missingInformation).toEqual(gaps);
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(reviewedPacket({ ...savedReview, missingInformation: gaps }))));
  expect((await presalesApi("token").get(packetId, new AbortController().signal)).rows[0].review?.missingInformation).toEqual(gaps);
  expect(reviewInputSchema.safeParse({ ...body, missingInformation: Array<string>(12).fill("𠀀".repeat(1000)) }).success).toBe(true);
  for (const missingInformation of [["确".repeat(1001)], Array<string>(157).fill("确认"), [...Array<string>(24).fill("确".repeat(500)), "认"]]) {
    expect(reviewInputSchema.safeParse({ ...body, missingInformation }).success).toBe(false);
  }
});
function reviewedPacket(review: Record<string, unknown>, history: Record<string, unknown>[] = [review]) {
  return {
    id: packetId, title: "Review contract", createdAt: "2026-10-08T00:00:00Z", rowCount: 1, staleSources: false,
    sources: [{ versionId: otherId, documentId: otherId, generationId: otherId, filename: "contract.txt", versionNumber: 1, latestVersionNumber: 1, contentSha256: "a".repeat(64), applicability: "测试资料" }],
    rows: [{ id: rowId, requirement: { key: "R1", text: "核对验收", sourceLocation: "" }, revision: 2, state: "drafted", attempts: [], review, reviewHistory: history,
      draft: { ...reviewedText, prerequisites: [...reviewedText.prerequisites, { condition: "附加前提。", state: "met", citationIndexes: [0] }],
        citations: [{ chunkId: otherId, documentVersionId: otherId, excerpt: "验收状态未登记。", filename: "contract.txt", pageNumber: 1, heading: null, startOffset: 0, endOffset: 8 }], retrieval: [] },
    }],
  };
}

describe("Presales review source bindings at the HTTP boundary", () => {
  it("binds each review to its own citation list and rejects invalid historical links", async () => {
    const original = reviewedPacket(savedReview).rows[0].draft.citations[0];
    const corrected = { ...savedReview, citations: [original, { ...original, chunkId: rowId, excerpt: "Correct clause." }], prerequisites: [{ ...reviewedText.prerequisites[0], citationIndexes: [1] }] };
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(reviewedPacket(corrected))));
    expect((await presalesApi("token").get(packetId, new AbortController().signal)).rows[0].review?.citations?.[1].excerpt).toBe("Correct clause.");
    fetch.mockResolvedValue(new Response(JSON.stringify(reviewedPacket(corrected, [{ ...corrected, citations: [original] }, corrected]))));
    await expect(presalesApi("token").get(packetId, new AbortController().signal)).rejects.toThrow();
  });

  it("validates explicit selection, duplication and conflict versions before a review write", () => {
    const citation = { chunkId: rowId, documentVersionId: otherId, excerpt: "Original source." };
    const body = { ...reviewedText, expectedRevision: 1, note: "Correct evidence.", citations: [citation] };
    expect(reviewInputSchema.safeParse(body).success).toBe(true);
    for (const patch of [{ citations: [] }, { citations: [citation, citation] }, { status: "conflicting_evidence" }, { citations: [{ ...citation, filename: "forged.txt" }] }]) {
      expect(reviewInputSchema.safeParse({ ...body, ...patch }).success).toBe(false);
    }
    expect(reviewInputSchema.safeParse({ ...body, citations: [], prerequisites: [], conditions: [], status: "insufficient_evidence", missingInformation: ["Need source."] }).success).toBe(true);
  });
  it("preserves split, human-added and excluded origins on read and accepts older responses", async () => {
    const revised = { ...savedReview, prerequisites: [reviewedText.prerequisites[0], reviewedText.prerequisites[0], reviewedText.prerequisites[0]], prerequisiteChanges: { origins: [0, 0, null], excludedIndexes: [1] } };
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(reviewedPacket(revised))));
    const result = await presalesApi("test-token").get(packetId, new AbortController().signal);
    expect(result.rows[0].review?.prerequisiteChanges).toEqual(revised.prerequisiteChanges);
    const legacy: Record<string, unknown> = { ...savedReview };
    delete legacy.prerequisiteChanges;
    fetch.mockResolvedValue(new Response(JSON.stringify(reviewedPacket(legacy))));
    expect((await presalesApi("test-token").get(packetId, new AbortController().signal)).rows[0].review?.prerequisiteChanges).toBeUndefined();
  });

  it.each([
    { prerequisiteChanges: { origins: [], excludedIndexes: [0, 1] } },
    { prerequisiteChanges: { origins: [0], excludedIndexes: [] } },
    { prerequisiteChanges: { origins: [0], excludedIndexes: [0, 1] } },
    { prerequisiteChanges: { origins: [2], excludedIndexes: [1] } },
    { prerequisiteChanges: { origins: [true], excludedIndexes: [1] } },
    { prerequisiteChanges: { origins: [0], excludedIndexes: [1, 1] } },
    { prerequisites: null },
    { prerequisites: [{ ...reviewedText.prerequisites[0], citationIndexes: [1] }] },
    { prerequisites: [{ ...reviewedText.prerequisites[0], excerpt: "伪造引文" }] },
  ])("rejects malformed current and historical review bindings before rendering", async patch => {
    const invalid = { ...savedReview, ...patch };
    const fetch = vi.spyOn(globalThis, "fetch");
    for (const body of [reviewedPacket(invalid), reviewedPacket(savedReview, [invalid, savedReview])]) {
      fetch.mockResolvedValue(new Response(JSON.stringify(body)));
      await expect(presalesApi("test-token").get(packetId, new AbortController().signal)).rejects.toThrow();
    }
  });
});

describe("Presales saved execution policy boundary", () => {
  const policy = { version: "presales.execution.v1", mode: "auto", rowTimeoutSeconds: 90, queueTimeoutSeconds: 900, maxProviderRequests: 1, dailyDispatchLimit: 200,
    routes: [{ route: "primary", provider: "openai_compatible", endpointSha256: "a".repeat(64), modelName: "fixture", modelVersion: null, modelRevision: null, reasoningEffort: null, streaming: false, timeoutSeconds: 30, maxOutputBytes: 262144, promptVersion: "presales.v12", promptSha256: "b".repeat(64) }] };
  it.each([
    { mode: "unlimited" }, { version: "presales.execution.v999" }, { maxProviderRequests: 3 },
    { rowTimeoutSeconds: 901 }, { dailyDispatchLimit: 0 }, { maxProviderRequests: 2 },
    { routes: [policy.routes[0], policy.routes[0]], maxProviderRequests: 2 },
    { routes: [{ ...policy.routes[0], timeoutSeconds: 301 }] },
    { routes: [{ ...policy.routes[0], apiKey: "should-never-be-in-a-response" }] },
  ])("rejects malformed saved strategies before displaying task content", async patch => {
    const original = reviewedPacket(savedReview);
    const body = { ...original, rows: original.rows.map(row => ({ ...row, attempts: [{ id: attemptId, number: 1, state: "succeeded", errorCode: null, modelProvider: "openai_compatible", modelName: "fixture", providerRequestCount: 1, provenance: {}, usage: null, createdAt: "2026-10-08T00:00:00Z", finishedAt: "2026-10-08T00:01:00Z", deadlineAt: "2026-10-08T00:02:00Z", executionPolicy: { ...policy, ...patch } }] })) };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(body)));
    await expect(presalesApi("test-token").get(packetId, new AbortController().signal)).rejects.toThrow();
  });
});
