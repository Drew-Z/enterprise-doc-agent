import { z, type ZodType } from "zod";
import { errorResponseSchema } from "../agent/api/schemas";
import { authenticatedFetch, type ApiCredential } from "../auth/transport";
import { workbookMetadataSchema, workbookPreviewSchema, workbookMime, type ConfirmedWorkbook, type WorkbookUpload } from "./workbook";

const id = z.string().uuid();
const time = z.iso.datetime({ offset: true });
export const responseStatus = z.enum(["supported", "conditional", "contradicted", "insufficient_evidence", "conflicting_evidence"]);
export const prerequisiteState = z.enum(["met", "unmet", "unknown"]);
const prerequisite = z.object({ condition: z.string().trim().min(1).max(1000), state: prerequisiteState, citationIndexes: z.array(z.number().int().min(0).max(11)).min(1).max(12).refine(indexes => new Set(indexes).size === indexes.length) }).strict();
// Match Python's Unicode code-point budget, not JavaScript's UTF-16 length.
const missingInformation = z.array(z.string().trim().min(1).refine(text => Array.from(text).length <= 1000)).max(12 * 12 + 12).refine(items => items.reduce((total, text) => total + Array.from(text).length, 0) <= 12000);
const responseFields = { status: responseStatus, answer: z.string().min(1).max(4000), conditions: z.array(z.string()).max(12), missingInformation, prerequisites: z.array(prerequisite).max(12).nullable().default(null) };
const evidence = z.object({ chunkId: id, documentVersionId: id, excerpt: z.string().min(1).max(600), filename: z.string(), pageNumber: z.number().int().nullable(), heading: z.string().nullable(), startOffset: z.number().int().nonnegative(), endOffset: z.number().int().nonnegative() }).strict();
const retrieval = z.object({ versionId: id, retrievedCount: z.number().int().nonnegative(), usedCount: z.number().int().nonnegative(), truncated: z.boolean() }).strict();
const draft = z.object({ ...responseFields, citations: z.array(evidence), retrieval: z.array(retrieval) }).strict().refine(value => validPrerequisiteIndexes(value.prerequisites, value.citations.length));
const originalIndex = z.number().int().min(0).max(11);
const prerequisiteChanges = z.object({ origins: z.array(originalIndex.nullable()).max(12), excludedIndexes: z.array(originalIndex).max(12).refine(values => new Set(values).size === values.length) }).strict();
const reviewFields = { ...responseFields, prerequisiteChanges: prerequisiteChanges.nullable().optional() };
const review = z.object({ ...reviewFields, revision: z.number().int().positive(), note: z.string(), actorId: id, reviewedAt: time, citations: z.array(evidence).max(12).nullable().optional() }).strict();
const manualAuthorship = z.object({ actorId: id, createdAt: time, note: z.string().min(1).max(1000) }).strict();
const manualEvidencePage = z.object({ items: z.array(evidence).max(10), nextOffset: z.number().int().min(0).max(100000).nullable() }).strict();
export const executionModeSchema = z.enum(["auto", "deep"]);
const digest = z.string().regex(/^[0-9a-f]{64}$/);
const routePolicy = z.object({ route: z.enum(["primary", "fallback"]), provider: z.literal("openai_compatible"), endpointSha256: digest, modelName: z.string().min(1).max(200), modelVersion: z.string().nullable(), modelRevision: z.string().nullable(), reasoningEffort: z.enum(["low", "medium", "high", "xhigh"]).nullable(), streaming: z.boolean(), timeoutSeconds: z.number().positive().max(300), maxOutputBytes: z.number().int().min(1024).max(4 * 1024 ** 2), promptVersion: z.string(), promptSha256: digest }).strict();
const executionPolicy = z.object({ version: z.literal("presales.execution.v1"), mode: executionModeSchema, rowTimeoutSeconds: z.number().positive().max(900), queueTimeoutSeconds: z.number().min(30).max(3600), maxProviderRequests: z.number().int().min(1).max(2), dailyDispatchLimit: z.number().int().min(1).max(10000), routes: z.array(routePolicy).min(1).max(2) }).strict().refine(value => value.routes.length === value.maxProviderRequests && new Set(value.routes.map(route => route.route)).size === value.routes.length);
const attempt = z.object({ id, number: z.number().int().positive(), state: z.enum(["queued", "running", "recovering", "succeeded", "failed", "expired"]), errorCode: z.string().nullable(), modelProvider: z.string(), modelName: z.string().nullable(), providerRequestCount: z.number().int().min(0).max(2).nullable(), provenance: z.record(z.string(), z.string().nullable()), usage: z.record(z.string(), z.number().int().nonnegative().nullable()).nullable(), createdAt: time, finishedAt: time.nullable(), deadlineAt: time, executionPolicy: executionPolicy.nullable().optional() }).strict();
const requirement = z.object({ key: z.string().regex(/^[A-Za-z0-9_-]{1,40}$/), text: z.string().min(1).max(2000), sourceLocation: z.string().max(300) }).strict();
const sourceInput = z.object({ versionId: id, applicability: z.string().min(1).max(500) }).strict();
const source = sourceInput.extend({ documentId: id, generationId: id, filename: z.string(), versionNumber: z.number().int().positive(), latestVersionNumber: z.number().int().positive(), contentSha256: z.string().regex(/^[0-9a-f]{64}$/) });
const row = z.object({ id, requirement, revision: z.number().int().nonnegative(), state: z.enum(["pending", "queued", "running", "recovering", "drafted", "failed"]), draft: draft.nullable(), manualAuthorship: manualAuthorship.nullable().optional(), review: review.nullable(), reviewHistory: z.array(review), attempts: z.array(attempt).max(3) }).strict().refine(value => (!value.manualAuthorship || (value.draft !== null && !value.attempts.some(a => a.state === "succeeded"))) && [value.review, ...value.reviewHistory].every(entry => validPrerequisiteIndexes(entry?.prerequisites ?? null, (entry?.citations ?? value.draft?.citations ?? []).length) && validReviewChanges(entry?.prerequisiteChanges, entry?.prerequisites ?? null, value.draft?.prerequisites?.length ?? 0)));
export const packetSummarySchema = z.object({ id, title: z.string(), createdAt: time, rowCount: z.number().int().nonnegative(), staleSources: z.boolean() }).strict();
export const packetSchema = packetSummarySchema.extend({ sources: z.array(source).min(1).max(6), rows: z.array(row).min(1).max(120), generationMode: z.enum(["synchronous", "background"]).default("synchronous"), availableExecutionModes: z.array(executionModeSchema).max(2).refine(values => new Set(values).size === values.length).optional(), workbook: workbookMetadataSchema.nullable().optional() });
export const createPacketSchema = z.object({ title: z.string().trim().min(1).max(160), sources: z.array(sourceInput).min(1).max(6), requirements: z.array(requirement).min(1).max(12) }).strict();
const citationInput = evidence.pick({ chunkId: true, documentVersionId: true, excerpt: true });
export const reviewInputSchema = z.object({ ...reviewFields, expectedRevision: z.number().int().positive(), note: z.string().max(1000), citations: z.array(citationInput).max(12).nullable().optional() }).strict().refine(value =>
  (value.status !== "conditional" || value.conditions.length > 0)
  && (value.status !== "insufficient_evidence" || value.missingInformation.length > 0)
  && (value.status !== "supported" || value.conditions.length === 0)
  && (value.prerequisites === null || JSON.stringify(value.conditions) === JSON.stringify(prerequisiteConditions(value.prerequisites)))
  && (!value.prerequisiteChanges || (value.prerequisites !== null && value.prerequisiteChanges.origins.length === value.prerequisites.length))
  && (value.citations == null || (
    validPrerequisiteIndexes(value.prerequisites, value.citations.length)
    && (value.status === "insufficient_evidence" || value.citations.length > 0)
    && (value.status !== "conflicting_evidence" || new Set(value.citations.map(c => c.documentVersionId)).size >= 2)
    && new Set(value.citations.map(c => JSON.stringify(c))).size === value.citations.length
  ))
);
export type Packet = z.infer<typeof packetSchema>;
export type PresalesRow = z.infer<typeof row>;
export type CreatePacket = z.infer<typeof createPacketSchema>;
export type WorkbookImportInput = ConfirmedWorkbook & Pick<CreatePacket, "title" | "sources">;
export type ReviewInput = z.input<typeof reviewInputSchema>;
export const manualResponseSchema = z.object({ ...responseFields, expectedRevision: z.number().int().nonnegative(), note: z.string().trim().min(1).max(1000), citations: z.array(evidence.pick({ chunkId: true, documentVersionId: true, excerpt: true })).max(12) }).strict().refine(({ citations, ...value }) =>
  reviewInputSchema.safeParse({ ...value, expectedRevision: 1 }).success
  && validPrerequisiteIndexes(value.prerequisites, citations.length)
  && (value.status === "insufficient_evidence" || citations.length > 0)
  && (value.status !== "conflicting_evidence" || new Set(citations.map(c => c.documentVersionId)).size >= 2)
  && new Set(citations.map(c => JSON.stringify(c))).size === citations.length
);
export type ManualResponseInput = z.infer<typeof manualResponseSchema>;
export type ManualEvidencePage = z.infer<typeof manualEvidencePage>;
export type ResponseStatus = z.infer<typeof responseStatus>;
export type PrerequisiteAssessment = z.infer<typeof prerequisite>;
export type Evidence = z.infer<typeof evidence>;
export type PrerequisiteChanges = z.infer<typeof prerequisiteChanges>;
export type ExecutionMode = z.infer<typeof executionModeSchema>;
export function reviewCitations(draft: PresalesRow["draft"], review: PresalesRow["review"]): Evidence[] {
  return review?.citations ?? draft?.citations ?? [];
}
export function prerequisiteConditions(items: PrerequisiteAssessment[]): string[] {
  return [...new Set(items.filter(item => item.state !== "met").map(item => item.condition))];
}
function validPrerequisiteIndexes(items: PrerequisiteAssessment[] | null, count: number): boolean {
  return (items ?? []).every(item => item.citationIndexes.every(index => index < count));
}
export function validReviewChanges(changes: PrerequisiteChanges | null | undefined, items: PrerequisiteAssessment[] | null, count: number): boolean {
  if (!changes) return true;
  if (items === null || changes.origins.length !== items.length) return false;
  const included = new Set(changes.origins.filter((index): index is number => index !== null));
  const excluded = new Set(changes.excludedIndexes);
  return [...included, ...excluded].every(index => index < count)
    && [...included].every(index => !excluded.has(index))
    && included.size + excluded.size === count;
}
export const generationActive = (row: PresalesRow) => ["queued", "running", "recovering"].includes(row.state);
const batchSchema = z.object({ packet: packetSchema, rejected: z.array(z.object({ rowId: id, code: z.string() }).strict()) }).strict();
const admissionSchema = z.object({ rowId: id, disposition: z.enum(["enqueued", "replayed", "already_drafted"]), attemptId: id.nullable() }).strict().refine(value => (value.disposition === "already_drafted") === (value.attemptId === null));
const receiptSchema = z.object({ packetId: id, admissions: z.array(admissionSchema).max(12), rejected: z.array(z.object({ rowId: id, code: z.string() }).strict()).max(12) }).strict();
function receiptFor(packetId: string, rowIds: string[]) {
  return receiptSchema.refine(value => {
    const returned = [...value.admissions, ...value.rejected].map(item => item.rowId);
    return value.packetId === packetId && returned.length === rowIds.length && new Set(returned).size === rowIds.length && rowIds.every(rowId => returned.includes(rowId));
  });
}

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
  const request = async <T>(route: string, schema: ZodType<T>, signal: AbortSignal, method = "GET", payload?: unknown, key?: string, timeoutMs = 15_000): Promise<T> => {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (payload !== undefined) headers["Content-Type"] = "application/json";
    if (key) headers["Idempotency-Key"] = key;
    const timeout = AbortSignal.timeout(timeoutMs);
    try {
      const response = await authenticatedFetch(base() + route, token, { method, headers, body: payload === undefined ? undefined : JSON.stringify(payload), signal: AbortSignal.any([signal, timeout]), cache: "no-store" });
      await check(response);
      return schema.parse(await response.json());
    } catch (error) {
      if (timeout.aborted && !signal.aborted) throw new PresalesApiError(504, "presales_response_timeout", "The response is taking longer than expected. Read the saved sheet to check its current state.", null);
      throw error;
    }
  };
  const packetPath = (value: string) => "/" + id.parse(value);
  return {
    previewWorkbook: (payload: WorkbookUpload, signal: AbortSignal) => request("/workbooks/preview", workbookPreviewSchema, signal, "POST", payload),
    importWorkbook: (payload: WorkbookImportInput, key: string, signal: AbortSignal) => request("/workbooks", packetSchema, signal, "POST", payload, key),
    exportWorkbook: async (packetId: string, mode: "draft" | "reviewed", signal: AbortSignal) => {
      const response = await authenticatedFetch(base() + packetPath(packetId) + "/workbook?mode=" + mode, token, { headers: { Accept: workbookMime }, signal, cache: "no-store" });
      await check(response);
      if (!response.headers.get("Content-Type")?.startsWith(workbookMime)) throw new Error("Invalid workbook response.");
      return response.blob();
    },
    list: (signal: AbortSignal) => request("", z.array(packetSummarySchema), signal),
    get: (packetId: string, signal: AbortSignal) => request(packetPath(packetId), packetSchema, signal),
    create: (payload: CreatePacket, key: string, signal: AbortSignal) => request("", packetSchema, signal, "POST", createPacketSchema.parse(payload), key),
    generate: (packetId: string, rowId: string, key: string, signal: AbortSignal, background = false, executionMode?: ExecutionMode) => request(packetPath(packetId) + "/rows/" + id.parse(rowId) + "/generate", packetSchema, signal, "POST", executionMode ? { executionMode: executionModeSchema.parse(executionMode) } : undefined, key, background ? 15_000 : 180_000),
    generateBatch: (packetId: string, rowIds: string[], key: string, signal: AbortSignal, executionMode?: ExecutionMode) => request(packetPath(packetId) + "/generate", batchSchema, signal, "POST", { rowIds: z.array(id).min(1).max(12).parse(rowIds), ...(executionMode ? { executionMode: executionModeSchema.parse(executionMode) } : {}) }, key),
    admit: (packetId: string, rowId: string, key: string, signal: AbortSignal, executionMode?: ExecutionMode) => request(packetPath(packetId) + "/rows/" + id.parse(rowId) + "/generate?response=receipt", receiptFor(packetId, [rowId]), signal, "POST", executionMode ? { executionMode: executionModeSchema.parse(executionMode) } : undefined, key),
    admitBatch: (packetId: string, rowIds: string[], key: string, signal: AbortSignal, executionMode?: ExecutionMode) => request(packetPath(packetId) + "/generate?response=receipt", receiptFor(packetId, rowIds), signal, "POST", { rowIds: z.array(id).min(1).max(12).refine(values => new Set(values).size === values.length).parse(rowIds), ...(executionMode ? { executionMode: executionModeSchema.parse(executionMode) } : {}) }, key),
    review: (packetId: string, rowId: string, payload: ReviewInput, key: string, signal: AbortSignal) => request(packetPath(packetId) + "/rows/" + id.parse(rowId) + "/review", packetSchema, signal, "PUT", reviewInputSchema.parse(payload), key),
    manualResponse: (packetId: string, rowId: string, payload: ManualResponseInput, key: string, signal: AbortSignal) => request(packetPath(packetId) + "/rows/" + id.parse(rowId) + "/manual-response", packetSchema, signal, "PUT", manualResponseSchema.parse(payload), key),
    manualEvidence: (packetId: string, versionId: string, query: string, offset: number, signal: AbortSignal) => request(packetPath(packetId) + "/manual-evidence?" + new URLSearchParams({ versionId: id.parse(versionId), query, offset: String(offset) }).toString(), manualEvidencePage, signal),
    export: async (packetId: string, mode: "draft" | "reviewed", signal: AbortSignal) => {
      const response = await authenticatedFetch(base() + packetPath(packetId) + "/export?mode=" + mode, token, { headers: { Accept: "text/csv" }, signal, cache: "no-store" });
      await check(response);
      if (!response.headers.get("Content-Type")?.startsWith("text/csv")) throw new Error("Invalid export response.");
      return response.blob();
    },
  };
}
