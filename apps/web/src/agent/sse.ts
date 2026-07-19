import {
  agentSseDataSchema,
  validateSsePayload,
  type AgentEventType,
} from "./api/schemas";

export interface AgentTimelineEvent {
  seq: number;
  eventType: AgentEventType;
  eventVersion: number;
  payload: Record<string, unknown>;
  createdAt: string;
}

export class AgentSseProtocolError extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "AgentSseProtocolError";
  }
}

export class AgentSseSequenceGapError extends AgentSseProtocolError {
  constructor(
    readonly expected: number,
    readonly received: number,
  ) {
    super(`Agent event stream expected sequence ${expected} but received ${received}.`);
    this.name = "AgentSseSequenceGapError";
  }
}

interface SseFrame {
  id: string | null;
  event: string | null;
  data: string;
}

interface MutableSseFrame {
  id: string | null;
  event: string | null;
  dataLines: string[];
  touched: boolean;
}

function emptyFrame(): MutableSseFrame {
  return { id: null, event: null, dataLines: [], touched: false };
}

function finishFrame(frame: MutableSseFrame): SseFrame | null {
  if (!frame.touched || frame.dataLines.length === 0) {
    return null;
  }
  return { id: frame.id, event: frame.event, data: frame.dataLines.join("\n") };
}

function applyLine(frame: MutableSseFrame, line: string): void {
  if (line.startsWith(":")) {
    return;
  }
  const separator = line.indexOf(":");
  const field = separator === -1 ? line : line.slice(0, separator);
  let value = separator === -1 ? "" : line.slice(separator + 1);
  if (value.startsWith(" ")) {
    value = value.slice(1);
  }
  frame.touched = true;
  if (field === "id") {
    frame.id = value;
  } else if (field === "event") {
    frame.event = value;
  } else if (field === "data") {
    frame.dataLines.push(value);
  }
}

export async function* parseSseFrames(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<SseFrame> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let frame = emptyFrame();
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let newline = buffer.indexOf("\n");
      while (newline !== -1) {
        let line = buffer.slice(0, newline);
        buffer = buffer.slice(newline + 1);
        if (line.endsWith("\r")) {
          line = line.slice(0, -1);
        }
        if (line === "") {
          const completed = finishFrame(frame);
          frame = emptyFrame();
          if (completed !== null) {
            yield completed;
          }
        } else {
          applyLine(frame, line);
        }
        newline = buffer.indexOf("\n");
      }
      if (done) {
        break;
      }
    }
    if (buffer !== "") {
      applyLine(frame, buffer.endsWith("\r") ? buffer.slice(0, -1) : buffer);
    }
    const completed = finishFrame(frame);
    if (completed !== null) {
      yield completed;
    }
  } finally {
    reader.releaseLock();
  }
}

export async function* readAgentEventStream(
  response: Response,
  afterSequence: number,
): AsyncGenerator<AgentTimelineEvent> {
  if (!response.ok) {
    throw new AgentSseProtocolError(`Agent event stream returned HTTP ${response.status}.`);
  }
  const contentType = response.headers.get("Content-Type")?.toLowerCase() ?? "";
  if (!contentType.startsWith("text/event-stream")) {
    throw new AgentSseProtocolError("Agent event stream returned an invalid content type.");
  }
  if (response.body === null) {
    throw new AgentSseProtocolError("Agent event stream returned no response body.");
  }
  if (!Number.isSafeInteger(afterSequence) || afterSequence < 0) {
    throw new AgentSseProtocolError("Agent event cursor must be a non-negative safe integer.");
  }

  let cursor = afterSequence;
  for await (const frame of parseSseFrames(response.body)) {
    if (frame.id === null || !/^(0|[1-9][0-9]*)$/.test(frame.id)) {
      throw new AgentSseProtocolError("Agent event stream returned an invalid event ID.");
    }
    const sequence = Number(frame.id);
    if (!Number.isSafeInteger(sequence) || sequence <= 0) {
      throw new AgentSseProtocolError("Agent event stream returned an unsafe event ID.");
    }
    if (sequence <= cursor) {
      continue;
    }
    if (sequence !== cursor + 1) {
      throw new AgentSseSequenceGapError(cursor + 1, sequence);
    }
    let raw: unknown;
    try {
      raw = JSON.parse(frame.data) as unknown;
    } catch (error) {
      throw new AgentSseProtocolError("Agent event stream returned invalid JSON.", { cause: error });
    }
    const data = agentSseDataSchema.safeParse(raw);
    if (!data.success) {
      throw new AgentSseProtocolError("Agent event stream returned an invalid envelope.", {
        cause: data.error,
      });
    }
    if (frame.event !== data.data.eventType) {
      throw new AgentSseProtocolError("Agent event name does not match the data envelope.");
    }
    let payload: Record<string, unknown>;
    try {
      payload = validateSsePayload(data.data.eventType, data.data.payload);
    } catch (error) {
      throw new AgentSseProtocolError("Agent event payload failed validation.", { cause: error });
    }
    cursor = sequence;
    yield {
      seq: sequence,
      eventType: data.data.eventType,
      eventVersion: data.data.eventVersion,
      payload,
      createdAt: data.data.createdAt,
    };
  }
}

export function isTerminalAgentEvent(event: AgentTimelineEvent): boolean {
  return event.eventType === "run.cancelled" || event.eventType === "run.finished";
}

export function eventResponseToTimeline(
  event: {
    seq: number;
    eventType: AgentEventType;
    eventVersion: number;
    publicPayload: Record<string, unknown>;
    createdAt: string;
  },
): AgentTimelineEvent {
  return {
    seq: event.seq,
    eventType: event.eventType,
    eventVersion: event.eventVersion,
    payload: validateSsePayload(event.eventType, event.publicPayload),
    createdAt: event.createdAt,
  };
}

export function abortableDelay(milliseconds: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) {
    return Promise.reject(new DOMException("Aborted", "AbortError"));
  }
  return new Promise((resolve, reject) => {
    const handleAbort = () => {
      window.clearTimeout(timeout);
      reject(new DOMException("Aborted", "AbortError"));
    };
    const timeout = window.setTimeout(() => {
      signal.removeEventListener("abort", handleAbort);
      resolve();
    }, milliseconds);
    signal.addEventListener("abort", handleAbort, { once: true });
  });
}
