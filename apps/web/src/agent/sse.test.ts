import { describe, expect, it } from "vitest";

import {
  AgentSseProtocolError,
  AgentSseSequenceGapError,
  readAgentEventStream,
} from "./sse";

const createdAt = "2026-07-19T00:00:00Z";

function responseFromChunks(chunks: string[]): Response {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream; charset=utf-8" } },
  );
}

async function collect(response: Response, cursor = 0) {
  const events = [];
  for await (const event of readAgentEventStream(response, cursor)) events.push(event);
  return events;
}

describe("Agent fetch-SSE parser", () => {
  it("parses partial frames, CRLF, comments, and multi-line data", async () => {
    const json = JSON.stringify({
      createdAt,
      eventType: "run.started",
      eventVersion: 1,
      payload: { status: "running" },
    });
    const split = json.indexOf('"eventType"');
    const response = responseFromChunks([
      `: heartbeat\r\n\r\nid: 1\r\nevent: run.started\r\ndata: ${json.slice(0, split)}\r\n`,
      `data: ${json.slice(split)}\r\n\r\n`,
    ]);

    const events = await collect(response);

    expect(events).toEqual([
      {
        seq: 1,
        eventType: "run.started",
        eventVersion: 1,
        payload: { status: "running" },
        createdAt,
      },
    ]);
  });

  it("ignores duplicate sequence IDs and preserves the next ordered event", async () => {
    const event = (seq: number, type: "run.started" | "run.resumed") =>
      `id: ${seq}\nevent: ${type}\ndata: ${JSON.stringify({ createdAt, eventType: type, eventVersion: 1, payload: { status: "running" } })}\n\n`;
    const events = await collect(responseFromChunks([event(1, "run.started"), event(1, "run.started"), event(2, "run.resumed")]));

    expect(events.map((item) => item.seq)).toEqual([1, 2]);
  });

  it("rejects sequence gaps before accepting the out-of-order payload", async () => {
    const response = responseFromChunks([
      `id: 2\nevent: run.started\ndata: ${JSON.stringify({ createdAt, eventType: "run.started", eventVersion: 1, payload: { status: "running" } })}\n\n`,
    ]);

    await expect(collect(response)).rejects.toEqual(new AgentSseSequenceGapError(1, 2));
  });

  it("rejects event-name mismatches and unknown payload fields", async () => {
    const mismatch = responseFromChunks([
      `id: 1\nevent: run.resumed\ndata: ${JSON.stringify({ createdAt, eventType: "run.started", eventVersion: 1, payload: { status: "running" } })}\n\n`,
    ]);
    const leaked = responseFromChunks([
      `id: 1\nevent: run.started\ndata: ${JSON.stringify({ createdAt, eventType: "run.started", eventVersion: 1, payload: { status: "running", token: "secret" } })}\n\n`,
    ]);

    await expect(collect(mismatch)).rejects.toBeInstanceOf(AgentSseProtocolError);
    await expect(collect(leaked)).rejects.toBeInstanceOf(AgentSseProtocolError);
  });
});
