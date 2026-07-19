import { beforeEach, describe, expect, it } from "vitest";

import { AGENT_RUN_STORAGE_KEY, AgentPersistenceError, createAgentRunRecoveryStore } from "./persistence";

const runId = "11111111-1111-4111-8111-111111111111";

beforeEach(() => localStorage.clear());

describe("Agent run recovery storage", () => {
  it("persists only the run identifier and validated sequence cursor", () => {
    const store = createAgentRunRecoveryStore(localStorage);
    store.save({ version: 1, runId, lastSequence: 7 });

    expect(store.load()).toEqual({ version: 1, runId, lastSequence: 7 });
    expect(JSON.parse(localStorage.getItem(AGENT_RUN_STORAGE_KEY) ?? "null")).toEqual({
      version: 1,
      runId,
      lastSequence: 7,
    });
  });

  it("clears records containing prompt, token, citation, or signed URL fields", () => {
    localStorage.setItem(
      AGENT_RUN_STORAGE_KEY,
      JSON.stringify({
        version: 1,
        runId,
        lastSequence: 2,
        token: "secret",
        prompt: "raw prompt",
        citation: "document text",
        url: "https://signed.test/object",
      }),
    );

    expect(createAgentRunRecoveryStore(localStorage).load()).toBeNull();
    expect(localStorage.getItem(AGENT_RUN_STORAGE_KEY)).toBeNull();
  });

  it("rejects invalid writes", () => {
    const store = createAgentRunRecoveryStore(localStorage);
    expect(() => store.save({ version: 1, runId, lastSequence: -1 })).toThrow(AgentPersistenceError);
  });
});
