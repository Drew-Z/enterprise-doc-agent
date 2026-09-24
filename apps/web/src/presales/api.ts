import { z, type ZodType } from "zod";
import { errorResponseSchema } from "../agent/api/schemas";
import { authenticatedFetch, type ApiCredential } from "../auth/transport";

const id = z.string().uuid();
const time = z.iso.datetime({ offset: true });
export const responseStatus = z.enum(["supported", "conditional", "contradicted", "insufficient_evidence", "conflicting_evidence"]);
const responseFields = { status: responseStatus, answer: z.string().min(1).max(4000), conditions: z.array(z.string()).max(12), missingInformation: z.array(z.string()).max(12) };
const evidence = z.object({ chunkId: id, documentVersionId: id, excerpt: z.string().min(1).max(600), filename: z.string(), pageNumber: z.number().int().nullable(), heading: z.string().nullable(), startOffset: z.number().int().nonnegative(), endOffset: z.number().int().nonnegative() }).strict();
const retrieval = z.object({ versionId: id, retrievedCount: z.number().int().nonnegative(), usedCount: z.number().int().nonnegative(), truncated: z.boolean() }).strict();
const draft = z.object({ ...responseFields, citations: z.array(evidence), retrieval: z.array(retrieval) }).strict();
const review = z.object({ ...responseFields, revision: z.number().int().positive(), note: z.string(), actorId: id, reviewedAt: time }).strict();
const attempt = z.object({ id, number: z.number().int().positive(), state: z.enum(["queued", "running", "recovering", "succeeded", "failed", "expired"]), errorCode: z.string().nullable(), modelProvider: z.string(), modelName: z.string().nullable(), providerRequestCount: z.number().int().min(0).max(2).nullable(), provenance: z.record(z.string(), z.string().nullable()), usage: z.record(z.string(), z.number().int().nonnegative().nullable()).nullable(), createdAt: time, finishedAt: time.nullable(), deadlineAt: time }).strict();
const requirement = z.object({ key: z.string().regex(/^[A-Za-z0-9_-]{1,40}$/), text: z.string().min(1).max(2000), sourceLocation: z.string().max(300) }).strict();
const sourceInput = z.object({ versionId: id, applicability: z.string().min(1).max(500) }).strict();
const source = sourceInput.extend({ documentId: id, generationId: id, filename: z.string(), versionNumber: z.number().int().positive(), latestVersionNumber: z.number().int().positive(), contentSha256: z.string().regex(/^[0-9a-f]{64}$/) });
const row = z.object({ id, requirement, revision: z.number().int().nonnegative(), state: z.enum(["pending", "queued", "running", "recovering", "drafted", "failed"]), draft: draft.nullable(), review: review.nullable(), reviewHistory: z.array(review), attempts: z.array(attempt).max(3) }).strict();
export const packetSummarySchema = z.object({ id, title: z.string(), createdAt: time, rowCount: z.number().int().nonnegative(), staleSources: z.boolean() }).strict();
export const packetSchema = packetSummarySchema.extend({ sources: z.array(source).min(1).max(6), rows: z.array(row).min(1).max(12), generationMode: z.enum(["synchronous", "background"]).default("synchronous") });
export const createPacketSchema = z.object({ title: z.string().trim().min(1).max(160), sources: z.array(sourceInput).min(1).max(6), requirements: z.array(requirement).min(1).max(12) }).strict();
export const reviewInputSchema = z.object({ ...responseFields, expectedRevision: z.number().int().positive(), note: z.string().max(1000) }).strict();
export type Packet = z.infer<typeof packetSchema>;
export type PresalesRow = z.infer<typeof row>;
export type CreatePacket = z.infer<typeof createPacketSchema>;
export type ReviewInput = z.infer<typeof reviewInputSchema>;
export type ResponseStatus = z.infer<typeof responseStatus>;
export const generationActive = (row: PresalesRow) => ["queued", "running", "recovering"].includes(row.state);
const batchSchema = z.object({ packet: packetSchema, rejected: z.array(z.object({ rowId: id, code: z.string() }).strict()) }).strict();

export class PresalesApiError extends Error {
  constructor(readonly status: number, readonly code: string, message: string, readonly requestId: string | null) { super(message); this.name = "PresalesApiError"; }
}

const base = () => (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/+$/, "") + "/api/presales";
async function check(response: Response): Promise<void> {
  if (response.ok) return;
  const error = errorResponseSchema.safeParse(await response.json().catch(() => null));
  throw new PresalesApiError(response.status, error.success ? error.data.error.code : `presales_http_${response.status}`, error.success ? error.data.error.message : `Request failed (HTTP ${response.status}).`, error.success ? error.data.error.requestId : response.headers.get("X-Request-ID"));
}

export function presalesApi(token: ApiCredential) {
  const request = async <T>(route: string, schema: ZodType<T>, signal: AbortSignal, method = "GET", payload?: unknown, key?: string): Promise<T> => {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (payload !== undefined) headers["Content-Type"] = "application/json";
    if (key) headers["Idempotency-Key"] = key;
    const response = await authenticatedFetch(base() + route, token, { method, headers, body: payload === undefined ? undefined : JSON.stringify(payload), signal, cache: "no-store" });
    await check(response);
    return schema.parse(await response.json());
  };
  const packetPath = (value: string) => "/" + id.parse(value);
  return {
    list: (signal: AbortSignal) => request("", z.array(packetSummarySchema), signal),
    get: (packetId: string, signal: AbortSignal) => request(packetPath(packetId), packetSchema, signal),
    create: (payload: CreatePacket, key: string, signal: AbortSignal) => request("", packetSchema, signal, "POST", createPacketSchema.parse(payload), key),
    generate: (packetId: string, rowId: string, key: string, signal: AbortSignal) => request(packetPath(packetId) + "/rows/" + id.parse(rowId) + "/generate", packetSchema, signal, "POST", undefined, key),
    generateBatch: (packetId: string, rowIds: string[], key: string, signal: AbortSignal) => request(packetPath(packetId) + "/generate", batchSchema, signal, "POST", { rowIds: z.array(id).min(1).max(12).parse(rowIds) }, key),
    review: (packetId: string, rowId: string, payload: ReviewInput, key: string, signal: AbortSignal) => request(packetPath(packetId) + "/rows/" + id.parse(rowId) + "/review", packetSchema, signal, "PUT", reviewInputSchema.parse(payload), key),
    export: async (packetId: string, mode: "draft" | "reviewed", signal: AbortSignal) => {
      const response = await authenticatedFetch(base() + packetPath(packetId) + "/export?mode=" + mode, token, { headers: { Accept: "text/csv" }, signal, cache: "no-store" });
      await check(response);
      if (!response.headers.get("Content-Type")?.startsWith("text/csv")) throw new Error("Invalid export response.");
      return response.blob();
    },
  };
}
