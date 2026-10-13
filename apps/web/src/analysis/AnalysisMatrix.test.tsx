import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AnalysisMatrix } from "./AnalysisMatrix";
import { snapshotSchema } from "./api";
import { matrixSchema } from "./executionApi";

const id = "10000000-0000-4000-8000-000000000001";
const timestamp = "2026-10-12T08:00:00Z";
const snapshot = snapshotSchema.parse({ id, projectId: id, projectRevision: 1, sourceManifestHash: "a".repeat(64), createdAt: timestamp,
  payload: { schemaVersion: "analysis.sources.v1", title: "Compare", sources: [], spec: { schemaVersion: "analysis.spec.v1", commonRequirements: [], objects: [{ id: "a", name: "Supplier A", sourceVersionIds: [] }], fields: ["confirmed", "missing", "failed"].map(key => ({ id: key, name: key, question: key, valueType: "text" })) } } });
const run = { id, projectId: id, snapshotId: id, status: "partial", totalUnits: 3, finishedUnits: 3, dispatches: 3, maxDispatches: 6, executionMode: "fixed", createdAt: timestamp, deadlineAt: timestamp, cancelRequested: false };
const baseResult = { action: "final", value: false, evidenceState: "supported", citations: [], conditions: [], reason: "", missingParameters: [], calculationId: null, calculations: [], comparisonState: "not_comparable", reviewState: "unreviewed", evidence: [] };
const matrix = matrixSchema.parse({ run, snapshot, units: [
  { id, objectId: "a", fieldId: "confirmed", status: "succeeded", dispatches: 1, errorCode: null, result: baseResult, search: null, inputRequest: null },
  { id, objectId: "a", fieldId: "missing", status: "succeeded", dispatches: 1, errorCode: null, result: { ...baseResult, action: "abstain", value: null, evidenceState: "insufficient", reason: "Tax rate is not supplied" }, search: null, inputRequest: null },
  { id, objectId: "a", fieldId: "failed", status: "failed", dispatches: 1, errorCode: "analysis_response_unknown", result: null, search: null, inputRequest: null },
] });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("keeps the full denominator and distinguishes false, missing and execution failure", async () => {
  let revoked = false;
  vi.spyOn(globalThis, "fetch").mockImplementation(input => {
    const path = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const content = path.endsWith("/matrix") ? matrix : [run];
    return Promise.resolve(new Response(JSON.stringify(revoked ? { error: { code: "analysis_not_found", message: "Not found", requestId: "test" } } : content), { status: revoked ? 404 : 200, headers: { "Content-Type": "application/json" } }));
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}><AnalysisMatrix token="test" contextKey="tenant:actor" projectId={id} snapshot={snapshot} enabled={false} /></QueryClientProvider>);
  expect(await screen.findByText("false", { exact: true })).toBeVisible();
  expect(screen.getByRole("status")).toHaveTextContent("3/3");
  expect(screen.getByText("Tax rate is not supplied", { selector: "p" })).toBeVisible();
  expect(screen.getByText(/No valid result/)).toBeVisible();
  revoked = true;
  fireEvent.click(screen.getByRole("button", { name: "Refresh results" }));
  await screen.findByRole("alert");
  expect(screen.queryByText("Tax rate is not supplied")).not.toBeInTheDocument();
});

it("separates mode idempotency and submits requested inputs before polling resumes", async () => {
  const requestId = "20000000-0000-4000-8000-000000000002";
  const waitingRun = { ...run, status: "waiting_input", totalUnits: 1, finishedUnits: 0, dispatches: 1, maxDispatches: 2, executionMode: "agent" };
  const waitingMatrix = matrixSchema.parse({
    run: waitingRun,
    snapshot,
    units: [{
      id, objectId: "a", fieldId: "confirmed", status: "waiting_input", dispatches: 1, errorCode: null,
      result: { ...baseResult, action: "need_input", value: null, evidenceState: "insufficient", reason: "Count required", missingParameters: ["deployment_count"] },
      search: null,
      inputRequest: { id: requestId, unitId: id, revision: 1, parameters: [{ name: "deployment_count", unit: "site" }], state: "open", canResume: true },
    }],
  });
  const queuedRun = { ...waitingRun, status: "queued" };
  const queuedMatrix = matrixSchema.parse({ ...waitingMatrix, run: queuedRun });
  const requests: Array<{ path: string; method: string; body: unknown; key: string | null }> = [];
  let inputSubmitted = false;
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const path = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const method = init?.method ?? "GET";
    requests.push({ path, method, body: typeof init?.body === "string" ? JSON.parse(init.body) : null, key: new Headers(init?.headers).get("Idempotency-Key") });
    if (method === "POST" && path.endsWith("/inputs")) {
      inputSubmitted = true;
      return Promise.resolve(new Response(JSON.stringify(queuedRun), { status: 202, headers: { "Content-Type": "application/json" } }));
    }
    if (method === "POST") return Promise.resolve(new Response(JSON.stringify({ ...waitingRun, id: requestId }), { status: 202, headers: { "Content-Type": "application/json" } }));
    const content = path.endsWith("/matrix") ? (inputSubmitted ? queuedMatrix : waitingMatrix) : [waitingRun];
    return Promise.resolve(new Response(JSON.stringify(content), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}><AnalysisMatrix token="test" contextKey="tenant:actor" projectId={id} snapshot={snapshot} enabled /></QueryClientProvider>);
  expect(await screen.findByText("Count required")).toBeVisible();
  fireEvent.change(screen.getByRole("textbox", { name: "deployment_count (site)" }), { target: { value: "3" } });
  fireEvent.click(screen.getByRole("button", { name: "Submit and resume" }));
  await waitFor(() => expect(requests.some(item => item.path.endsWith("/inputs"))).toBe(true));
  expect(requests.find(item => item.path.endsWith("/inputs"))?.body).toEqual({ submissions: [{ requestId, expectedRevision: 1, values: { deployment_count: "3" } }] });
  await waitFor(() => expect(requests.filter(item => item.path.endsWith("/matrix")).length).toBeGreaterThan(1));
  expect(await screen.findByRole("status")).toHaveTextContent("queued");

  fireEvent.click(screen.getByRole("radio", { name: "Limited Agent" }));
  fireEvent.click(screen.getByRole("button", { name: "Start / open this source run" }));
  await waitFor(() => expect(requests.some(item => item.key === `analysis-agent-v1:${id}`)).toBe(true));
  const started = requests.find(item => item.key === `analysis-agent-v1:${id}`);
  expect(started?.body).toEqual({ snapshotId: id, executionMode: "agent", baseRunId: null });
});

it("uses a versioned incremental idempotency key and shows reuse and invalidation counts", async () => {
  const baseRunId = "60000000-0000-4000-8000-000000000006";
  const currentRunId = "70000000-0000-4000-8000-000000000007";
  const baseUnitId = "80000000-0000-4000-8000-000000000008";
  const oldSnapshotId = "a0000000-0000-4000-8000-00000000000a";
  const baseRun = { ...run, id: baseRunId, snapshotId: oldSnapshotId };
  const currentRun = {
    ...run,
    id: currentRunId,
    snapshotId: id,
    totalUnits: 2,
    finishedUnits: 2,
    dispatches: 1,
    maxDispatches: 4,
    baseRunId,
    reusedUnits: 1,
    affectedUnits: 1,
  };
  const baseMatrix = matrixSchema.parse({
    run: { ...baseRun, totalUnits: 2, finishedUnits: 2, dispatches: 2, maxDispatches: 4 },
    snapshot,
    units: [{
      id: baseUnitId, objectId: "a", fieldId: "confirmed", status: "succeeded", dispatches: 1,
      errorCode: null, result: baseResult, search: null, inputRequest: null,
    }],
  });
  const currentMatrix = matrixSchema.parse({
    run: currentRun,
    snapshot,
    units: [
      {
        id: id, objectId: "a", fieldId: "confirmed", status: "succeeded", dispatches: 0,
        errorCode: null, result: baseResult, search: null, inputRequest: null,
        reusedFromUnitId: baseUnitId,
      },
      {
        id: baseUnitId, objectId: "a", fieldId: "missing", status: "succeeded", dispatches: 1,
        errorCode: null, result: { ...baseResult, reason: "Recomputed after a parameter change" },
        search: null, inputRequest: null, invalidationReasons: ["parameter_changed:seats"],
      },
    ],
  });
  const requests: Array<{ path: string; method: string; body: unknown; key: string | null }> = [];
  let started = false;
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const path = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const method = init?.method ?? "GET";
    const headers = new Headers(init?.headers);
    const body = typeof init?.body === "string" ? JSON.parse(init.body) as unknown : null;
    requests.push({ path, method, body, key: headers.get("Idempotency-Key") });
    if (method === "POST") {
      started = true;
      return Promise.resolve(new Response(JSON.stringify(currentRun), { status: 202, headers: { "Content-Type": "application/json" } }));
    }
    if (path.endsWith("/matrix")) {
      return Promise.resolve(new Response(JSON.stringify(started ? currentMatrix : baseMatrix), { status: 200, headers: { "Content-Type": "application/json" } }));
    }
    return Promise.resolve(new Response(JSON.stringify(started ? [currentRun, baseRun] : [baseRun]), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}><AnalysisMatrix token="test" contextKey="tenant:actor" projectId={id} snapshot={snapshot} enabled /></QueryClientProvider>);
  expect(await screen.findByRole("status")).toHaveTextContent("2/2");
  fireEvent.click(screen.getByRole("button", { name: "Start / open this source run" }));
  await waitFor(() => expect(requests.some(item => item.method === "POST")).toBe(true));
  const startedRequest = requests.find(item => item.method === "POST");
  expect(startedRequest?.key).toBe("analysis-fixed-v2:" + id + ":" + baseRunId);
  expect(startedRequest?.body).toEqual({ snapshotId: id, executionMode: "fixed", baseRunId });
  expect(await screen.findByRole("status")).toHaveTextContent("reused 1 · recomputed 1");
  expect(await screen.findByText("Recomputed: parameter_changed:seats")).toBeVisible();
  expect(await screen.findByText("Reused from the previous run; review again")).toBeVisible();
});

it("creates the first user revision when a terminal cell has no model result", async () => {
  const unitId = "20000000-0000-4000-8000-000000000002";
  const revisionId = "30000000-0000-4000-8000-000000000003";
  const failedSnapshot = snapshotSchema.parse({ ...snapshot, payload: { ...snapshot.payload, spec: { ...snapshot.payload.spec, fields: [{ id: "failed", name: "Failed field", question: "Confirm manually", valueType: "text" }] } } });
  const failedRun = { ...run, totalUnits: 1, finishedUnits: 1, dispatches: 1, maxDispatches: 2, status: "failed" };
  const userResult = { ...baseResult, value: "Buyer confirmed", evidenceState: "insufficient", reason: "Entered after model failure", valueOrigin: "user_provided" };
  let revised = false;
  const requests: Array<{ path: string; method: string; body: Record<string, unknown> | null; match: string | null }> = [];
  const matrixForState = () => matrixSchema.parse({ run: failedRun, snapshot: failedSnapshot, units: [{
    id: unitId, objectId: "a", fieldId: "failed", status: "failed", dispatches: 1, errorCode: "analysis_output_invalid",
    result: revised ? userResult : null, search: null, inputRequest: null,
    currentRevision: revised ? 1 : null, currentRevisionId: revised ? revisionId : null, currentContentHash: revised ? "c".repeat(64) : null, authorType: revised ? "user" : null, review: null,
  }] });
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const path = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const method = init?.method ?? "GET";
    const headers = new Headers(init?.headers);
    const body = typeof init?.body === "string" ? JSON.parse(init.body) as Record<string, unknown> : null;
    requests.push({ path, method, body, match: headers.get("If-Match") });
    if (method === "POST" && path.endsWith(`/cells/${unitId}/revisions`)) {
      revised = true;
      return Promise.resolve(new Response(JSON.stringify({ id: revisionId, unitId, revision: 1, result: userResult, contentHash: "c".repeat(64), authorType: "user", actorId: id, reason: "Entered after model failure", createdAt: timestamp, supersedesId: null, review: null }), { status: 201, headers: { "Content-Type": "application/json" } }));
    }
    const content = path.endsWith("/matrix") ? matrixForState() : path.endsWith("/artifacts") || path.endsWith("/revisions") ? [] : [failedRun];
    return Promise.resolve(new Response(JSON.stringify(content), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}><AnalysisMatrix token="test" contextKey="tenant:actor" projectId={id} snapshot={failedSnapshot} enabled /></QueryClientProvider>);
  expect(await screen.findByText(/No valid result/)).toBeVisible();
  fireEvent.click(screen.getByText("Revise and review"));
  fireEvent.change(screen.getByRole("textbox", { name: "Confirmed value" }), { target: { value: "Buyer confirmed" } });
  fireEvent.change(screen.getByRole("textbox", { name: "Revision reason" }), { target: { value: "Entered after model failure" } });
  fireEvent.click(screen.getByRole("button", { name: "Save first revision" }));
  await waitFor(() => expect(requests.some(request => request.method === "POST" && request.path.endsWith("/revisions"))).toBe(true));
  const revisionRequest = requests.find(request => request.method === "POST" && request.path.endsWith("/revisions"));
  expect(revisionRequest?.match).toBe('"0"');
  expect(revisionRequest?.body).toMatchObject({ value: "Buyer confirmed", valueOrigin: "user_provided" });
  expect(await screen.findByText("Buyer confirmed", { exact: true })).toBeVisible();
  expect(screen.getByDisplayValue("Buyer confirmed")).toBeVisible();
});

it("revises, reviews, freezes, approves and downloads one exact matrix", async () => {
  const unitId = "20000000-0000-4000-8000-000000000002";
  const revisionId = "30000000-0000-4000-8000-000000000003";
  const reviewId = "40000000-0000-4000-8000-000000000004";
  const artifactId = "50000000-0000-4000-8000-000000000005";
  const oneSnapshot = snapshotSchema.parse({ ...snapshot, payload: { ...snapshot.payload, spec: { ...snapshot.payload.spec, fields: [{ id: "confirmed", name: "Confirmed", question: "Confirmed?", valueType: "text" }] } } });
  const oneRun = { ...run, totalUnits: 1, finishedUnits: 1, dispatches: 1, maxDispatches: 2, status: "completed" };
  const modelResult = { ...baseResult, value: "Original", valueOrigin: "document_evidence" };
  const userResult = { ...baseResult, value: "Buyer confirmed", evidenceState: "insufficient", reason: "Buyer supplied this value", valueOrigin: "user_provided" };
  let currentRevision = 1;
  let approved = false;
  let artifactStatus: "ready" | "approved" | null = null;
  const requests: Array<{ path: string; method: string; body: Record<string, unknown> | null; key: string | null; match: string | null }> = [];
  const review = { id: reviewId, cellRevisionId: revisionId, revision: 2, contentHash: "c".repeat(64), decision: "approved", reason: "Checked", actorId: id, createdAt: timestamp };
  const matrixForState = () => matrixSchema.parse({ run: oneRun, snapshot: oneSnapshot, units: [{
    id: unitId, objectId: "a", fieldId: "confirmed", status: "succeeded", dispatches: 1, errorCode: null,
    result: { ...(currentRevision === 1 ? modelResult : userResult), reviewState: approved ? "approved" : "unreviewed" }, search: null, inputRequest: null,
    currentRevision, currentRevisionId: currentRevision === 1 ? id : revisionId, currentContentHash: currentRevision === 1 ? "b".repeat(64) : "c".repeat(64), authorType: currentRevision === 1 ? "model" : "user", review: approved ? review : null,
  }] });
  const revisionForState = () => ({ id: currentRevision === 1 ? id : revisionId, unitId, revision: currentRevision, result: currentRevision === 1 ? modelResult : userResult, contentHash: currentRevision === 1 ? "b".repeat(64) : "c".repeat(64), authorType: currentRevision === 1 ? "model" : "user", actorId: currentRevision === 1 ? null : id, reason: currentRevision === 1 ? "Generated" : "Buyer supplied this value", createdAt: timestamp, supersedesId: currentRevision === 1 ? null : id, review: approved ? review : null });
  const artifact = () => ({ id: artifactId, runId: id, mode: "reviewed", status: artifactStatus, format: "xlsx", matrixRevision: 1, matrixHash: "d".repeat(64), sourceManifestHash: "a".repeat(64), cellRevisionIds: [revisionId], objectBucket: "artifacts", objectKey: `analysis/${artifactId}.xlsx`, filename: "reviewed.xlsx", contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", contentHash: "e".repeat(64), sizeBytes: 4096, createdAt: timestamp });
  const opened = vi.spyOn(window, "open").mockImplementation(() => null);
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const path = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const method = init?.method ?? "GET";
    const headers = new Headers(init?.headers);
    const body = typeof init?.body === "string" ? JSON.parse(init.body) as Record<string, unknown> : null;
    requests.push({ path, method, body, key: headers.get("Idempotency-Key"), match: headers.get("If-Match") });
    let content: unknown;
    let status = 200;
    if (method === "POST" && path.endsWith(`/cells/${unitId}/revisions`)) { currentRevision = 2; content = revisionForState(); status = 201; }
    else if (method === "POST" && path.endsWith(`/cells/${unitId}/reviews`)) { approved = true; content = review; status = 201; }
    else if (method === "POST" && path.endsWith(`/runs/${id}/exports`)) { artifactStatus = "ready"; content = artifact(); status = 201; }
    else if (method === "POST" && path.endsWith(`/artifacts/${artifactId}/approvals`)) { artifactStatus = "approved"; content = { id: reviewId, artifactId, artifactHash: "e".repeat(64), sourceManifestHash: "a".repeat(64), matrixHash: "d".repeat(64), operation: "deliver", decision: "approved", reason: "Approved", actorId: id, expiresAt: "2026-10-12T09:00:00Z", decidedAt: timestamp, consumedAt: timestamp }; status = 201; }
    else if (path.endsWith(`/artifacts/${artifactId}/download`)) content = { artifactId, filename: "reviewed.xlsx", contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", contentHash: "e".repeat(64), sizeBytes: 4096, url: "https://download.test/reviewed.xlsx", expiresInSeconds: 300 };
    else if (path.endsWith(`/cells/${unitId}/revisions`)) content = [revisionForState()];
    else if (path.endsWith(`/runs/${id}/artifacts`)) content = artifactStatus ? [artifact()] : [];
    else if (path.endsWith("/matrix")) content = matrixForState();
    else content = [oneRun];
    return Promise.resolve(new Response(JSON.stringify(content), { status, headers: { "Content-Type": "application/json" } }));
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}><AnalysisMatrix token="test" contextKey="tenant:actor" projectId={id} snapshot={oneSnapshot} enabled /></QueryClientProvider>);
  await screen.findByText("Original");
  expect(screen.getByRole("button", { name: "Create reviewed XLSX" })).toBeDisabled();
  fireEvent.click(screen.getByText("Revise and review"));
  fireEvent.change(screen.getByRole("textbox", { name: "Confirmed value" }), { target: { value: "Buyer confirmed" } });
  fireEvent.change(screen.getByRole("textbox", { name: "Revision reason" }), { target: { value: "Buyer supplied this value" } });
  fireEvent.click(screen.getByRole("button", { name: "Save new revision" }));
  await waitFor(() => expect(requests.some(request => request.method === "POST" && request.path.endsWith("/revisions"))).toBe(true));
  const revisionRequest = requests.find(request => request.method === "POST" && request.path.endsWith("/revisions"));
  expect(revisionRequest?.match).toBe('"1"');
  expect(revisionRequest?.key).toMatch(/^analysis-revision:/);
  await waitFor(() => expect(screen.getAllByText("Buyer confirmed", { exact: true })).toHaveLength(2));
  fireEvent.change(screen.getByRole("textbox", { name: "Review reason" }), { target: { value: "Checked" } });
  fireEvent.click(screen.getByRole("button", { name: "Approve revision" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Create reviewed XLSX" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Create reviewed XLSX" }));
  expect(await screen.findByText("reviewed.xlsx")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Approve delivery" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Download" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Download" }));
  await waitFor(() => expect(opened).toHaveBeenCalledWith("https://download.test/reviewed.xlsx", "_blank", "noopener,noreferrer"));
});
