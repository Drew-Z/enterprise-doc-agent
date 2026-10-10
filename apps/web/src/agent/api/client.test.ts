import { describe, expect, it, vi } from "vitest";

import {
  AgentApiClient,
  AgentApiProtocolError,
  AgentAuthenticationError,
} from "./client";

const runId = "11111111-1111-4111-8111-111111111111";
const artifactId = "22222222-2222-4222-8222-222222222222";

function completedRun(attemptFields: Record<string, unknown>) {
  const at = "2026-09-28T00:00:00Z";
  return {
    runId, tenantId: runId, documentVersionId: artifactId,
    taskType: "question_answer", publishRequested: false, status: "succeeded",
    graphVersion: "graph-v1", promptVersion: "prompt-v1", modelProvider: "openai_compatible",
    modelName: "fixture", modelVersion: null, modelRevision: null, fallbackTriggerCode: null,
    providerRequestCount: 1, providerUsageRequestCount: 1, promptTokens: 10,
    completionTokens: 5, totalTokens: 15, repairRequestCount: 0, fallbackCount: 0,
    breakerState: "closed", toolSchemaVersion: "tool-v1", currentExecutionSeq: 0,
    errorCode: null, createdAt: at, startedAt: at, waitingAt: null, finishedAt: at,
    cancelledAt: null,
    executions: [{
      executionId: artifactId, sequence: 0, kind: "initial", jobId: runId,
      jobStatus: "succeeded", attempts: 1, maxAttempts: 5, cancelRequested: false,
      attemptHistory: [{
        attemptId: runId, attemptNumber: 1, status: "succeeded", workerId: "worker-test",
        startedAt: at, heartbeatAt: at, finishedAt: at, errorCode: null, ...attemptFields,
      }],
    }],
  };
}

describe("Agent API client", () => {
  it("bounds a stalled JSON request and preserves timeout as recoverable", async () => {
    const deadline = new AbortController();
    const timer = vi.spyOn(AbortSignal, "timeout").mockReturnValueOnce(deadline.signal);
    try {
      const client = new AgentApiClient({ getToken: () => "token", fetcher: (_input, init) => new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
      }) });
      const pending = client.getRun(runId);
      deadline.abort();
      await expect(pending).rejects.toMatchObject({ code: "timeout" });
      expect(timer).toHaveBeenCalledWith(15_000);
    } finally { timer.mockRestore(); }
  });

  it.each([403, 429, 502, 503, 504])("preserves proxy HTTP %i without exposing its body", async status => {
    const client = new AgentApiClient({ getToken: () => "token", fetcher: () => Promise.resolve(new Response("<html>private upstream diagnostic</html>", { status, headers: { "X-Request-ID": "proxy-123" } })) });
    await expect(client.getRun(runId)).rejects.toMatchObject({ status, code: `agent_http_${status}`, requestId: "proxy-123", message: "The task service could not complete this request." });
  });

  it.each([null, "grounding.citation_excerpt_not_verbatim"])(
    "reads persisted execution history with diagnosticCode %s",
    async diagnosticCode => {
      const fetcher = vi.fn().mockResolvedValue(Response.json(completedRun({ diagnosticCode })));
      const client = new AgentApiClient({ getToken: () => "token", fetcher });

      const run = await client.getRun(runId);

      expect(run.status).toBe("succeeded");
      expect(run.executions[0].sequence).toBe(0);
      expect(run.executions[0].attemptHistory[0]).toMatchObject({ diagnosticCode });
      expect(fetcher).toHaveBeenCalledTimes(1);
    },
  );

  it("keeps reading older execution history without a diagnostic field", async () => {
    const client = new AgentApiClient({
      getToken: () => "token", fetcher: vi.fn().mockResolvedValue(Response.json(completedRun({}))),
    });
    await expect(client.getRun(runId)).resolves.toMatchObject({ status: "succeeded" });
  });

  it.each([{ diagnosticCode: 42 }, { diagnosticCode: null, rawException: "private detail" }])(
    "rejects malformed or undeclared attempt diagnostics",
    async fields => {
      const client = new AgentApiClient({
        getToken: () => "token", fetcher: vi.fn().mockResolvedValue(Response.json(completedRun(fields))),
      });
      await expect(client.getRun(runId)).rejects.toBeInstanceOf(AgentApiProtocolError);
    },
  );

  it("opens an authenticated event stream with Last-Event-ID", async () => {
    const fetcher = vi.fn(() =>
      Promise.resolve(new Response(": heartbeat\n\n", {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      })),
    );
    const client = new AgentApiClient({ baseUrl: "http://api.test", getToken: () => "token", fetcher });

    await client.openEventStream(runId, 9);

    const [url, init] = (fetcher.mock.calls[0] ?? []) as unknown as [string, RequestInit];
    expect(url).toBe(`http://api.test/api/agent-runs/${runId}/events/stream`);
    const headers = new Headers(init?.headers);
    expect(headers.get("Authorization")).toBe("Bearer token");
    expect(headers.get("Accept")).toBe("text/event-stream");
    expect(headers.get("Last-Event-ID")).toBe("9");
  });

  it("calls the injected fetcher without binding the API client as its receiver", async () => {
    let receiver: unknown = null;
    const fetcher = function (this: unknown) {
      // The receiver is the behavior under test; do not generalize this pattern.
      // eslint-disable-next-line @typescript-eslint/no-this-alias
      receiver = this;
      return Promise.resolve(new Response("[]", {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }));
    };
    const client = new AgentApiClient({ getToken: () => "token", fetcher });

    await client.listReadyDocumentVersions();

    expect(receiver).toBeUndefined();
  });

  it("requires a token before issuing any request", async () => {
    const fetcher = vi.fn();
    const client = new AgentApiClient({ getToken: () => null, fetcher });

    await expect(client.getRun(runId)).rejects.toBeInstanceOf(AgentAuthenticationError);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("rejects extra response fields through strict Zod schemas", async () => {
    const fetcher = vi.fn(() =>
      Promise.resolve(new Response(
        JSON.stringify([
          {
            versionId: runId,
            documentId: "22222222-2222-4222-8222-222222222222",
            generationId: "33333333-3333-4333-8333-333333333333",
            filename: "contract.pdf",
            sizeBytes: 100,
            contentSha256: "a".repeat(64),
            createdAt: "2026-07-19T00:00:00Z",
            objectKey: "private/key",
          },
        ]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )),
    );
    const client = new AgentApiClient({ getToken: () => "token", fetcher });

    await expect(client.listReadyDocumentVersions()).rejects.toBeInstanceOf(AgentApiProtocolError);
  });

  it("maps typed API errors without leaking an unvalidated body", async () => {
    const fetcher = vi.fn(() =>
      Promise.resolve(new Response(
        JSON.stringify({ error: { code: "approval_principal_forbidden", message: "Denied", requestId: null } }),
        { status: 403, headers: { "Content-Type": "application/json" } },
      )),
    );
    const client = new AgentApiClient({ getToken: () => "token", fetcher });

    await expect(client.getApproval(runId)).rejects.toMatchObject({
      status: 403,
      code: "approval_principal_forbidden",
    });
  });

  it("loads a strictly validated grounded artifact preview", async () => {
    const fetcher = vi.fn(() =>
      Promise.resolve(new Response(
        JSON.stringify({
          artifactId,
          runId,
          documentVersionId: "33333333-3333-4333-8333-333333333333",
          status: "published",
          contentSha256: "a".repeat(64),
          schemaVersion: 1,
          taskType: "question_answer",
          answerText: "Payment is due within 30 days.",
          structuredFields: null,
          riskHint: "low",
          citations: [{
            chunkId: "44444444-4444-4444-8444-444444444444",
            documentVersionId: "33333333-3333-4333-8333-333333333333",
            sourceFilename: "contract.pdf",
            pageNumber: 3,
            heading: "Payment terms",
            startOffset: 120,
            endOffset: 168,
            excerpt: "Invoices are payable within thirty calendar days.",
          }],
          behaviorVersions: {
            graphVersion: "graph-v1",
            promptVersion: "prompt-v1",
            toolSchemaVersion: "tool-v1",
          },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )),
    );
    const client = new AgentApiClient({ baseUrl: "http://api.test", getToken: () => "token", fetcher });

    const preview = await client.getArtifactPreview(artifactId);

    expect(preview.answerText).toBe("Payment is due within 30 days.");
    expect(preview.citations[0]?.pageNumber).toBe(3);
    const [url, init] = (fetcher.mock.calls[0] ?? []) as unknown as [string, RequestInit];
    expect(url).toBe(`http://api.test/api/agent-artifacts/${artifactId}`);
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer token");
  });
});
