import { describe, expect, it } from "vitest";

import type { CreateUploadResponse, GetUploadResponse } from "../api/schemas";
import type { HashResult } from "../hashing/protocol";
import { initialUploadState, reduceUpload } from "./reducer";
import type { PersistedUploadSession, UploadAction, UploadMachineState, UploadPhase } from "./types";

const sessionId = "11111111-1111-4111-8111-111111111111";
const documentId = "22222222-2222-4222-8222-222222222222";
const versionId = "33333333-3333-4333-8333-333333333333";
const wholeSha256 = "a".repeat(64);
const checksumOne = btoa(String.fromCharCode(...new Uint8Array(32)));
const checksumTwo = btoa(String.fromCharCode(...new Uint8Array(32).fill(1)));
const file = new File([new Uint8Array([1, 2, 3, 4, 5])], "contract.pdf", { type: "application/pdf" });
const initialHash: HashResult = {
  wholeSha256,
  parts: [{ partNumber: 1, sizeBytes: 5, checksumSha256: checksumOne }],
};
const partHash: HashResult = {
  wholeSha256,
  parts: [
    { partNumber: 1, sizeBytes: 3, checksumSha256: checksumOne },
    { partNumber: 2, sizeBytes: 2, checksumSha256: checksumTwo },
  ],
};
const created: CreateUploadResponse = {
  sessionId,
  status: "active",
  filename: file.name,
  extension: ".pdf",
  mediaType: "application/pdf",
  sizeBytes: file.size,
  declaredSha256: wholeSha256,
  partSizeBytes: 3,
  expectedPartCount: 2,
  expiresAt: "2026-07-18T00:00:00Z",
  replayed: false,
};
const persisted: PersistedUploadSession = {
  version: 1,
  sessionId,
  filename: file.name,
  sizeBytes: file.size,
  declaredSha256: wholeSha256,
  partSizeBytes: 3,
  expiresAt: "2026-07-18T00:00:00Z",
};

function startNewUpload(): UploadMachineState {
  const selected = reduceUpload(initialUploadState, {
    type: "select_file",
    file,
    mediaType: "application/pdf",
    idempotencyKey: "key-1",
  });
  const hashed = reduceUpload(selected.state, { type: "hash_succeeded", generation: 1, result: initialHash });
  const session = reduceUpload(hashed.state, { type: "session_created", generation: 1, session: created });
  return reduceUpload(session.state, { type: "hash_succeeded", generation: 1, result: partHash }).state;
}

function creatingState(): UploadMachineState {
  const selected = reduceUpload(initialUploadState, {
    type: "select_file",
    file,
    mediaType: "application/pdf",
    idempotencyKey: "key-1",
  });
  return reduceUpload(selected.state, { type: "hash_succeeded", generation: 1, result: initialHash }).state;
}

function completingState(): UploadMachineState {
  let state = startNewUpload();
  for (const partNumber of [1, 2]) {
    state = reduceUpload(state, { type: "part_presign_started", generation: 1, partNumber, attempt: 1 }).state;
    state = reduceUpload(state, { type: "part_upload_started", generation: 1, partNumber, attempt: 1 }).state;
    state = reduceUpload(state, {
      type: "part_uploaded",
      generation: 1,
      partNumber,
      attempt: 1,
      etag: `"etag-${partNumber}"`,
    }).state;
  }
  return state;
}

function stateForPhase(phase: UploadPhase): UploadMachineState {
  switch (phase) {
    case "idle":
      return initialUploadState;
    case "awaiting_file":
      return reduceUpload(initialUploadState, { type: "restore_session", session: persisted }).state;
    case "hashing":
      return reduceUpload(initialUploadState, {
        type: "select_file",
        file,
        mediaType: "application/pdf",
        idempotencyKey: "key-1",
      }).state;
    case "creating":
      return creatingState();
    case "uploading":
      return startNewUpload();
    case "paused":
      return reduceUpload(startNewUpload(), { type: "pause" }).state;
    case "completing":
      return completingState();
    case "completed":
      return reduceUpload(completingState(), {
        type: "complete_succeeded",
        generation: 1,
        result: {
          sessionId,
          status: "completed",
          documentId,
          versionId,
          completedAt: "2026-07-17T12:00:00Z",
          replayed: false,
        },
      }).state;
    case "failed":
      return reduceUpload(creatingState(), {
        type: "session_create_failed",
        generation: 1,
        code: "network_error",
        message: "Failed.",
      }).state;
    case "canceled":
      return reduceUpload(stateForPhase("hashing"), { type: "cancel" }).state;
  }
}

describe("reduceUpload", () => {
  it("runs the new-upload flow through two bounded hash passes", () => {
    const selected = reduceUpload(initialUploadState, {
      type: "select_file",
      file,
      mediaType: "application/pdf",
      idempotencyKey: "key-1",
    });
    expect(selected.state.phase).toBe("hashing");
    expect(selected.effects).toEqual([
      expect.objectContaining({ type: "hash_file", mode: "initial", partSizeBytes: file.size }),
    ]);

    const progress = reduceUpload(selected.state, { type: "hash_progress", generation: 1, processedBytes: 4, totalBytes: 5 });
    const regressed = reduceUpload(progress.state, { type: "hash_progress", generation: 1, processedBytes: 2, totalBytes: 5 });
    expect(regressed.state.hashProcessedBytes).toBe(4);

    const hashed = reduceUpload(regressed.state, { type: "hash_succeeded", generation: 1, result: initialHash });
    expect(hashed.state.phase).toBe("creating");
    expect(hashed.effects[0]).toMatchObject({ type: "create_session", request: { sha256: wholeSha256 } });

    const session = reduceUpload(hashed.state, { type: "session_created", generation: 1, session: created });
    expect(session.state.phase).toBe("hashing");
    expect(session.effects.map((effect) => effect.type)).toEqual(["persist_session", "hash_file"]);
    expect(session.effects[1]).toMatchObject({ mode: "parts", partSizeBytes: 3 });

    const partitioned = reduceUpload(session.state, { type: "hash_succeeded", generation: 1, result: partHash });
    expect(partitioned.state.phase).toBe("uploading");
    expect(partitioned.state.parts.map((part) => [part.startByte, part.endByte])).toEqual([[0, 3], [3, 5]]);
    expect(partitioned.effects[0]).toMatchObject({ type: "queue_parts", generation: 1 });
  });

  it("pauses without aborting the server session and ignores old-generation messages", () => {
    let state = startNewUpload();
    state = reduceUpload(state, { type: "part_presign_started", generation: 1, partNumber: 1, attempt: 1 }).state;
    state = reduceUpload(state, { type: "part_upload_started", generation: 1, partNumber: 1, attempt: 1 }).state;
    const paused = reduceUpload(state, { type: "pause" });

    expect(paused.state.phase).toBe("paused");
    expect(paused.state.parts[0]).toMatchObject({ status: "pending", uploadedBytes: 0 });
    expect(paused.effects.map((effect) => effect.type)).toEqual(["pause_scheduler", "abort_transfers"]);
    expect(paused.effects.some((effect) => effect.type === "abort_session")).toBe(false);

    const stale = reduceUpload(paused.state, {
      type: "part_uploaded",
      generation: 1,
      partNumber: 1,
      attempt: 1,
      etag: '"late"',
    });
    expect(stale.accepted).toBe(false);
    expect(stale.state).toBe(paused.state);

    const resumed = reduceUpload(paused.state, { type: "resume" });
    expect(resumed.state.phase).toBe("uploading");
    expect(resumed.effects.map((effect) => effect.type)).toEqual(["resume_scheduler", "queue_parts"]);
  });

  it("retries a failed part with a new attempt and completes in ordered part order", () => {
    let state = startNewUpload();
    state = reduceUpload(state, { type: "part_presign_started", generation: 1, partNumber: 1, attempt: 1 }).state;
    state = reduceUpload(state, { type: "part_failed", generation: 1, partNumber: 1, attempt: 1, code: "network" }).state;
    const retried = reduceUpload(state, { type: "retry_part", partNumber: 1 });
    expect(retried.state.parts[0]).toMatchObject({ status: "pending", attempt: 2 });
    expect(retried.effects[0]).toMatchObject({ type: "queue_parts", parts: [expect.objectContaining({ attempt: 2 })] });

    state = reduceUpload(retried.state, { type: "part_presign_started", generation: 1, partNumber: 1, attempt: 2 }).state;
    state = reduceUpload(state, { type: "part_upload_started", generation: 1, partNumber: 1, attempt: 2 }).state;
    state = reduceUpload(state, { type: "part_uploaded", generation: 1, partNumber: 1, attempt: 2, etag: '"one"' }).state;
    state = reduceUpload(state, { type: "part_presign_started", generation: 1, partNumber: 2, attempt: 1 }).state;
    state = reduceUpload(state, { type: "part_upload_started", generation: 1, partNumber: 2, attempt: 1 }).state;
    state = reduceUpload(state, { type: "part_uploaded", generation: 1, partNumber: 2, attempt: 1, etag: '"two"' }).state;

    expect(state.phase).toBe("completing");
    const transition = reduceUpload(
      { ...state, phase: "completing" },
      {
        type: "complete_succeeded",
        generation: 1,
        result: {
          sessionId,
          status: "completed",
          documentId,
          versionId,
          completedAt: "2026-07-17T12:00:00Z",
          replayed: false,
        },
      },
    );
    expect(transition.state.phase).toBe("completed");
    expect(transition.effects).toEqual([{ type: "clear_persistence" }]);
  });

  it("cancel aborts local work and the server session, unlike pause", () => {
    const canceled = reduceUpload(startNewUpload(), { type: "cancel" });
    expect(canceled.state.phase).toBe("canceled");
    expect(canceled.effects.map((effect) => effect.type)).toEqual([
      "abort_hash",
      "abort_transfers",
      "clear_scheduler",
      "clear_persistence",
      "abort_session",
    ]);
  });

  it("compensates a create response that arrives after local cancellation", () => {
    const selected = reduceUpload(initialUploadState, {
      type: "select_file",
      file,
      mediaType: "application/pdf",
      idempotencyKey: "key-1",
    });
    const creating = reduceUpload(selected.state, { type: "hash_succeeded", generation: 1, result: initialHash });
    const canceled = reduceUpload(creating.state, { type: "cancel" });
    const late = reduceUpload(canceled.state, { type: "session_created", generation: 1, session: created });

    expect(late.state).toBe(canceled.state);
    expect(late.effects).toEqual([{ type: "abort_session", sessionId }]);
  });

  it.each([
    { type: "part_upload_started" as const, generation: 1, partNumber: 1, attempt: 1 },
    { type: "part_progress" as const, generation: 1, partNumber: 1, attempt: 1, uploadedBytes: 1 },
    { type: "part_uploaded" as const, generation: 1, partNumber: 1, attempt: 1, etag: '"etag"' },
    { type: "part_failed" as const, generation: 1, partNumber: 1, attempt: 1, code: "network" },
  ])("rejects part event $type from pending state", (action) => {
    const state = startNewUpload();
    const result = reduceUpload(state, action);
    expect(result.accepted).toBe(false);
    expect(result.state).toBe(state);
  });

  it("rejects stale attempts even in an otherwise legal part state", () => {
    let state = startNewUpload();
    state = reduceUpload(state, { type: "part_presign_started", generation: 1, partNumber: 1, attempt: 1 }).state;
    const stale = reduceUpload(state, { type: "part_upload_started", generation: 1, partNumber: 1, attempt: 2 });
    expect(stale.accepted).toBe(false);
    expect(stale.state).toBe(state);
  });

  it.each([
    { type: "pause" as const },
    { type: "resume" as const },
    { type: "retry_part" as const, partNumber: 1 },
    { type: "cancel" as const },
    { type: "clear" as const },
  ])("rejects illegal idle command $type", (action) => {
    const result = reduceUpload(initialUploadState, action);
    expect(result).toEqual({ accepted: false, state: initialUploadState, effects: [] });
  });

  it("rejects a different recovery file before any server or presign effect", () => {
    const persisted: PersistedUploadSession = {
      version: 1,
      sessionId,
      filename: "contract.pdf",
      sizeBytes: 5,
      declaredSha256: wholeSha256,
      partSizeBytes: 3,
      expiresAt: "2026-07-18T00:00:00Z",
    };
    const restored = reduceUpload(initialUploadState, { type: "restore_session", session: persisted });
    const wrong = reduceUpload(restored.state, { type: "reselect_file", file: new File(["wrong"], "other.pdf") });
    expect(wrong.state).toMatchObject({ phase: "failed", failure: { code: "different_file" } });
    expect(wrong.effects).toEqual([]);
  });

  it("hash-verifies the recovery file before fetching server state", () => {
    const restored = reduceUpload(initialUploadState, { type: "restore_session", session: persisted });
    const selected = reduceUpload(restored.state, { type: "reselect_file", file });
    expect(selected.effects[0]).toMatchObject({ type: "hash_file", mode: "resume" });

    const mismatch = reduceUpload(selected.state, {
      type: "hash_succeeded",
      generation: selected.state.generation,
      result: { ...partHash, wholeSha256: "b".repeat(64) },
    });
    expect(mismatch.state).toMatchObject({ phase: "failed", failure: { code: "different_file" } });
    expect(mismatch.effects).toEqual([]);

    const verified = reduceUpload(selected.state, {
      type: "hash_succeeded",
      generation: selected.state.generation,
      result: partHash,
    });
    expect(verified.effects).toEqual([{ type: "fetch_session", generation: 2, sessionId }]);

    const server: GetUploadResponse = {
      ...created,
      uploadedParts: [{ partNumber: 1, sizeBytes: 3, checksumSha256: checksumOne, etag: '"one"' }],
    };
    delete (server as Partial<CreateUploadResponse>).replayed;
    const reconciled = reduceUpload(verified.state, { type: "session_reconciled", generation: 2, session: server });
    expect(reconciled.state.parts.map((part) => part.status)).toEqual(["uploaded", "pending"]);
    expect(reconciled.effects[0]).toMatchObject({ type: "queue_parts", parts: [expect.objectContaining({ partNumber: 2 })] });
  });

  it("enforces the complete phase-by-command legality matrix", () => {
    const phases: UploadPhase[] = [
      "idle",
      "awaiting_file",
      "hashing",
      "creating",
      "uploading",
      "paused",
      "completing",
      "completed",
      "failed",
      "canceled",
    ];
    const cases: Array<{
      name: string;
      action: UploadAction;
      legal: readonly UploadPhase[];
    }> = [
      {
        name: "select_file",
        action: { type: "select_file", file, mediaType: "application/pdf", idempotencyKey: "key-2" },
        legal: ["idle", "completed", "failed", "canceled"],
      },
      { name: "restore_session", action: { type: "restore_session", session: persisted }, legal: ["idle"] },
      { name: "reselect_file", action: { type: "reselect_file", file }, legal: ["awaiting_file"] },
      { name: "pause", action: { type: "pause" }, legal: ["uploading"] },
      { name: "resume", action: { type: "resume" }, legal: ["paused"] },
      { name: "retry", action: { type: "retry" }, legal: ["failed"] },
      {
        name: "cancel",
        action: { type: "cancel" },
        legal: ["awaiting_file", "hashing", "creating", "uploading", "paused", "failed"],
      },
      { name: "clear", action: { type: "clear" }, legal: ["completed", "failed", "canceled"] },
    ];

    for (const command of cases) {
      for (const phase of phases) {
        expect(reduceUpload(stateForPhase(phase), command.action).accepted, `${command.name} in ${phase}`).toBe(
          command.legal.includes(phase),
        );
      }
    }
  });

  it("supports typed retry effects for hash, create, reconcile, and complete failures", () => {
    const hashing = stateForPhase("hashing");
    const hashFailure = reduceUpload(hashing, {
      type: "hash_failed",
      generation: hashing.generation,
      code: "read_failed",
      message: "Read failed.",
    });
    expect(reduceUpload(hashFailure.state, { type: "retry" }).effects[0]).toMatchObject({ type: "hash_file" });

    const createFailure = reduceUpload(creatingState(), {
      type: "session_create_failed",
      generation: 1,
      code: "network_error",
      message: "Failed.",
      requestId: "req-create-1",
    });
    expect(createFailure.state.failure?.requestId).toBe("req-create-1");
    expect(reduceUpload(createFailure.state, { type: "retry" }).effects[0]).toMatchObject({ type: "create_session" });

    const reconciling = reduceUpload(
      reduceUpload(stateForPhase("awaiting_file"), { type: "reselect_file", file }).state,
      { type: "hash_succeeded", generation: 2, result: partHash },
    );
    const reconcileFailure = reduceUpload(reconciling.state, {
      type: "session_reconcile_failed",
      generation: 2,
      code: "network_error",
      message: "Failed.",
    });
    expect(reduceUpload(reconcileFailure.state, { type: "retry" }).effects[0]).toMatchObject({ type: "fetch_session" });

    const completeFailure = reduceUpload(completingState(), {
      type: "complete_failed",
      generation: 1,
      code: "network_error",
      message: "Failed.",
    });
    expect(reduceUpload(completeFailure.state, { type: "retry" }).effects[0]).toMatchObject({ type: "complete_session" });
    expect(reduceUpload(completeFailure.state, { type: "cancel" }).accepted).toBe(false);
    expect(reduceUpload(completeFailure.state, { type: "clear" }).accepted).toBe(false);
  });

  it("aborts an active server session when create identity validation fails", () => {
    const mismatch = reduceUpload(creatingState(), {
      type: "session_created",
      generation: 1,
      session: { ...created, filename: "other.pdf" },
    });
    expect(mismatch.state).toMatchObject({ phase: "failed", failure: { code: "session_identity_mismatch" } });
    expect(mismatch.effects).toEqual([{ type: "abort_session", sessionId }]);
  });

  it("rejects malformed hash progress, part plans, and mismatched completion identities", () => {
    const hashing = stateForPhase("hashing");
    expect(
      reduceUpload(hashing, {
        type: "hash_progress",
        generation: hashing.generation,
        processedBytes: Number.NaN,
        totalBytes: file.size,
      }).accepted,
    ).toBe(false);

    const afterCreate = reduceUpload(creatingState(), { type: "session_created", generation: 1, session: created });
    const badPlan = reduceUpload(afterCreate.state, {
      type: "hash_succeeded",
      generation: 1,
      result: {
        wholeSha256,
        parts: [
          { partNumber: 1, sizeBytes: 2, checksumSha256: checksumOne },
          { partNumber: 2, sizeBytes: 3, checksumSha256: checksumTwo },
        ],
      },
    });
    expect(badPlan.state).toMatchObject({ phase: "failed", failure: { code: "invalid_part_plan" } });
    expect(badPlan.effects).toEqual([]);

    const wrongCompletion = reduceUpload(completingState(), {
      type: "complete_succeeded",
      generation: 1,
      result: {
        sessionId: "44444444-4444-4444-8444-444444444444",
        status: "completed",
        documentId,
        versionId,
        completedAt: "2026-07-17T12:00:00Z",
        replayed: false,
      },
    });
    expect(wrongCompletion.accepted).toBe(false);
  });
});
