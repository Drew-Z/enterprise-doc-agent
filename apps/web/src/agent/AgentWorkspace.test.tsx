import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createUploadTokenStore } from "../upload/persistence";
import { createAgentRunRecoveryStore } from "./persistence";
import type { AgentApiClientProtocol } from "./api/client";
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
  sessionStorage.clear();
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("AgentWorkspace", () => {
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
    expect(screen.getByText("Verified artifacts")).toBeInTheDocument();
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
