import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createUploadTokenStore } from "../upload/persistence";
import { createAgentRunRecoveryStore } from "./persistence";
import { AgentApiClient, type AgentApiClientProtocol } from "./api/client";
import type { AgentWorkspaceDependencies } from "./AgentWorkspace";
import { AgentWorkspace } from "./AgentWorkspace";

const runId = "11111111-1111-4111-8111-111111111111";
const versionId = "22222222-2222-4222-8222-222222222222";
const documentId = "33333333-3333-4333-8333-333333333333";
const generationId = "44444444-4444-4444-8444-444444444444";
const artifactId = "55555555-5555-4555-8555-555555555555";
const createdAt = "2026-07-19T00:00:00Z";

function runStatus(status: "running" | "succeeded") {
  return {
    runId,
    tenantId: "66666666-6666-4666-8666-666666666666",
    documentVersionId: versionId,
    taskType: "question_answer" as const,
    publishRequested: false,
    status,
    graphVersion: "graph-v1",
    promptVersion: "prompt-v1",
    modelProvider: "deterministic",
    modelName: "fixture",
    modelVersion: null,
    modelRevision: null,
    fallbackTriggerCode: null,
    providerRequestCount: 0,
    providerUsageRequestCount: 0,
    promptTokens: null,
    completionTokens: null,
    totalTokens: null,
    repairRequestCount: 0,
    fallbackCount: 0,
    breakerState: "closed",
    toolSchemaVersion: "tool-v1",
    currentExecutionSeq: 0,
    errorCode: null,
    createdAt,
    startedAt: createdAt,
    waitingAt: null,
    finishedAt: status === "succeeded" ? createdAt : null,
    cancelledAt: null,
    executions: [],
  };
}

function streamResponse(startSequence = 2): Response {
  const encoder = new TextEncoder();
  const frames = [
    `id: ${startSequence}\nevent: run.started\ndata: ${JSON.stringify({ createdAt, eventType: "run.started", eventVersion: 1, payload: { status: "running" } })}\n\n`,
    `id: ${startSequence + 1}\nevent: run.finished\ndata: ${JSON.stringify({ createdAt, eventType: "run.finished", eventVersion: 1, payload: { status: "succeeded", refusal_reason: null } })}\n\n`,
  ];
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const frame of frames) controller.enqueue(encoder.encode(frame));
        controller.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}

afterEach(() => {
  cleanup();
  sessionStorage.clear();
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("AgentWorkspace", () => {
  function httpWorkspace(fault?: (path: string, init: RequestInit) => Promise<Response> | Response | undefined, recoveryStorage?: Storage) {
    createUploadTokenStore(sessionStorage).save("local-token");
    const fetcher = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const path = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const response = fault?.(path, init);
      if (response !== undefined) return await response;
      if (path.endsWith("/ready-document-versions")) return Response.json([{ versionId, documentId, generationId, filename: "contract.pdf", sizeBytes: 2048, contentSha256: "a".repeat(64), createdAt }]);
      if (path === "/api/agent-runs") return Response.json({ runId, jobId: generationId, status: "pending", replayed: true, createdAt }, { status: 200 });
      if (path === `/api/agent-runs/${runId}`) return Response.json(runStatus("succeeded"));
      return Response.json([]);
    });
    const dependencies = { createApiClient: () => new AgentApiClient({ getToken: () => "local-token", fetcher }), idempotencyKeyFactory: vi.fn(() => crypto.randomUUID()), openExternal: vi.fn() };
    return { fetcher, dependencies, ...render(<AgentWorkspace dependencies={dependencies} recoveryStorage={recoveryStorage} />) };
  }

  async function submitRequest() {
    await screen.findByText("contract.pdf · 2.00 KiB");
    fireEvent.change(screen.getByLabelText("Document version"), { target: { value: versionId } });
    fireEvent.change(screen.getByLabelText("Request"), { target: { value: "Summarize the payment terms." } });
    fireEvent.click(screen.getByRole("button", { name: "Create run" }));
  }

  it("recovers a lost create response with the same key, including a later explicit retry", async () => {
    let posts = 0;
    const view = httpWorkspace(path => {
      if (path === "/api/agent-runs" && ++posts <= 2) return Promise.reject(new TypeError("Response lost"));
    });
    await submitRequest();
    expect(screen.getByText(/Submitting your task/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Creating" })).toBeDisabled();
    await screen.findByText(/Acceptance could not be confirmed/);
    fireEvent.click(screen.getByRole("button", { name: "Create run" }));
    await screen.findByText("Complete");
    const writes = view.fetcher.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(writes).toHaveLength(3);
    expect(new Set(writes.map(([, init]) => new Headers(init?.headers).get("Idempotency-Key"))).size).toBe(1);
    expect(view.dependencies.idempotencyKeyFactory).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("recovers a proxy 503 on refresh without another creation", async () => {
    createAgentRunRecoveryStore(localStorage).save({ version: 1, runId, lastSequence: 0 });
    let reads = 0;
    const view = httpWorkspace(path => {
      if (path === `/api/agent-runs/${runId}` && ++reads === 1) return new Response("<html>proxy unavailable</html>", { status: 503 });
    });
    await screen.findByText("Complete");
    expect(reads).toBe(2);
    expect(view.fetcher.mock.calls.every(([, init]) => (init?.method ?? "GET") === "GET")).toBe(true);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("does not retry denied reads or display a cached result", async () => {
    createAgentRunRecoveryStore(localStorage).save({ version: 1, runId, lastSequence: 0 });
    const view = httpWorkspace(path => path === `/api/agent-runs/${runId}` ? new Response("private proxy body", { status: 403 }) : undefined);
    await screen.findByRole("alert");
    expect(view.fetcher.mock.calls.filter(([path]) => path === `/api/agent-runs/${runId}`)).toHaveLength(1);
    expect(screen.queryByText("Complete")).not.toBeInTheDocument();
    expect(screen.queryByText(/private proxy body/)).not.toBeInTheDocument();
  });

  it("keeps an accepted task visible when recovery storage is full", async () => {
    const storage: Storage = { length: 0, key: () => null, getItem: () => null, removeItem: vi.fn(), clear: vi.fn(), setItem: () => { throw new Error("Full"); } };
    httpWorkspace(undefined, storage);
    await submitRequest();
    await screen.findByText("Complete");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("retires an in-flight create on unmount without restoring the old task", async () => {
    let finish!: (response: Response) => void;
    const view = httpWorkspace(path => path === "/api/agent-runs" ? new Promise<Response>(resolve => { finish = resolve; }) : undefined);
    await submitRequest();
    view.unmount();
    finish(Response.json({ runId, jobId: generationId, status: "pending", replayed: false, createdAt }, { status: 201 }));
    await waitFor(() => expect(localStorage.getItem("enterprise-doc.agent-run.v1")).toBeNull());
    const write = view.fetcher.mock.calls.find(([, init]) => init?.method === "POST");
    expect(write?.[1]?.signal?.aborted).toBe(true);
  });

  it("creates a run, replays its timeline, and downloads only through a fresh URL", async () => {
    const tokenStore = createUploadTokenStore(sessionStorage);
    tokenStore.save("local-token");
    const openExternal = vi.fn();
    const openEventStream = vi.fn().mockResolvedValue(streamResponse());
    const client: AgentApiClientProtocol = {
      listReadyDocumentVersions: vi.fn().mockResolvedValue([
        {
          versionId,
          documentId,
          generationId,
          filename: "contract.pdf",
          sizeBytes: 2048,
          contentSha256: "a".repeat(64),
          createdAt,
        },
      ]),
      createRun: vi.fn().mockResolvedValue({ runId, jobId: generationId, status: "pending", replayed: false, createdAt }),
      getRun: vi.fn().mockResolvedValueOnce(runStatus("running")).mockResolvedValueOnce(runStatus("succeeded")),
      listEvents: vi.fn().mockResolvedValue([
        {
          eventId: "77777777-7777-4777-8777-777777777777",
          seq: 1,
          eventType: "run.created",
          eventVersion: 1,
          publicPayload: { task_type: "question_answer", document_version_id: versionId, publish_requested: false },
          createdAt,
        },
      ]),
      openEventStream,
      cancelRun: vi.fn(),
      getApproval: vi.fn(),
      decideApproval: vi.fn(),
      listArtifacts: vi.fn().mockResolvedValue([
        {
          artifactId,
          runId,
          documentVersionId: versionId,
          kind: "answer",
          status: "draft_ready",
          contentType: "text/markdown",
          contentSha256: "b".repeat(64),
          sizeBytes: 128,
          createdAt,
          verifiedAt: createdAt,
          publishedAt: null,
        },
      ]),
      getArtifactPreview: vi.fn().mockResolvedValue({
        artifactId,
        runId,
        documentVersionId: versionId,
        status: "draft_ready",
        contentSha256: "b".repeat(64),
        schemaVersion: 1,
        taskType: "question_answer",
        answerText: "Payment is due within 30 days.",
        structuredFields: null,
        riskHint: "low",
        citations: [
          {
            chunkId: "88888888-8888-4888-8888-888888888888",
            documentVersionId: versionId,
            sourceFilename: "contract.pdf",
            pageNumber: 3,
            heading: "Payment terms",
            startOffset: 120,
            endOffset: 168,
            excerpt: "Invoices are payable within thirty calendar days.",
          },
        ],
        behaviorVersions: {
          graphVersion: "graph-v1",
          promptVersion: "prompt-v1",
          toolSchemaVersion: "tool-v1",
        },
      }),
      getArtifactDownload: vi.fn().mockResolvedValue({
        artifactId,
        status: "draft_ready",
        contentType: "text/markdown",
        contentSha256: "b".repeat(64),
        sizeBytes: 128,
        url: "https://object.test/signed-answer",
        expiresInSeconds: 300,
      }),
    };
    const dependencies: AgentWorkspaceDependencies = {
      createApiClient: () => client,
      idempotencyKeyFactory: () => "agent-test-1",
      openExternal,
    };

    render(<AgentWorkspace dependencies={dependencies} />);
    await screen.findByText("contract.pdf · 2.00 KiB");
    fireEvent.change(screen.getByLabelText("Document version"), { target: { value: versionId } });
    fireEvent.change(screen.getByLabelText("Request"), { target: { value: "Summarize the payment terms." } });
    fireEvent.click(screen.getByRole("button", { name: "Create run" }));

    expect(await screen.findByText("Run succeeded")).toBeInTheDocument();
    expect(screen.getByText("Verified result")).toBeInTheDocument();
    expect(screen.getByText("Payment is due within 30 days.")).toBeInTheDocument();
    expect(screen.getByText("contract.pdf · page 3 · Payment terms")).toBeInTheDocument();
    expect(screen.getByText("Invoices are payable within thirty calendar days.")).toBeInTheDocument();
    expect(screen.getByText("graph-v1")).toBeInTheDocument();
    expect(screen.getByText("Execution metadata")).toBeInTheDocument();
    expect(screen.getByText("deterministic · fixture")).toBeInTheDocument();
    expect(screen.getByText("0 requests", { exact: false })).toBeInTheDocument();
    expect(screen.queryByText("Loading events")).not.toBeInTheDocument();
    expect(openEventStream).toHaveBeenCalledWith(runId, 1, expect.any(AbortSignal));

    fireEvent.click(screen.getByRole("button", { name: "Download answer" }));
    await waitFor(() => expect(openExternal).toHaveBeenCalledWith("https://object.test/signed-answer"));

    const recovery = JSON.parse(localStorage.getItem("enterprise-doc.agent-run.v1") ?? "null") as Record<string, unknown>;
    expect(Object.keys(recovery).sort()).toEqual(["lastSequence", "runId", "version"]);
    expect(recovery).not.toHaveProperty("inputText");
    expect(recovery).not.toHaveProperty("url");
  });

  it("resumes from the persisted cursor and pages beyond the API event limit", async () => {
    const tokenStore = createUploadTokenStore(sessionStorage);
    tokenStore.save("local-token");
    createAgentRunRecoveryStore(localStorage).save({ version: 1, runId, lastSequence: 7 });

    const firstPage = Array.from({ length: 500 }, (_, index) => ({
      eventId: `${(index + 1).toString(16).padStart(8, "0")}-7777-4777-8777-777777777777`,
      seq: index + 8,
      eventType: "run.started" as const,
      eventVersion: 1,
      publicPayload: { status: "running" },
      createdAt,
    }));
    const finalEvent = {
      eventId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      seq: 508,
      eventType: "run.finished" as const,
      eventVersion: 1,
      publicPayload: { status: "succeeded", refusal_reason: null },
      createdAt,
    };
    const requestedCursors: number[] = [];
    const listEvents: AgentApiClientProtocol["listEvents"] = (_id, afterSequence = 0) => {
      requestedCursors.push(afterSequence);
      return Promise.resolve(afterSequence === 7 ? firstPage : [finalEvent]);
    };
    const openEventStream = vi.fn<AgentApiClientProtocol["openEventStream"]>();
    const client: AgentApiClientProtocol = {
      listReadyDocumentVersions: vi.fn().mockResolvedValue([]),
      createRun: vi.fn(),
      getRun: vi.fn().mockResolvedValue(runStatus("succeeded")),
      listEvents,
      openEventStream,
      cancelRun: vi.fn(),
      getApproval: vi.fn(),
      decideApproval: vi.fn(),
      listArtifacts: vi.fn().mockResolvedValue([]),
      getArtifactPreview: vi.fn(),
      getArtifactDownload: vi.fn(),
    };
    const dependencies: AgentWorkspaceDependencies = {
      createApiClient: () => client,
      idempotencyKeyFactory: () => "agent-recovery-test",
      openExternal: vi.fn(),
    };

    render(<AgentWorkspace dependencies={dependencies} />);

    await waitFor(() => expect(screen.getByText("Run succeeded")).toBeInTheDocument());
    expect(requestedCursors).toEqual([7, 507]);
    expect(openEventStream).not.toHaveBeenCalled();
    expect(screen.getByText("#508")).toBeInTheDocument();
  });
});
