import type { UploadedPart } from "../api/schemas";
import { compareFileMetadata, compareHashedFileIdentity } from "../fileIdentity";
import type { HashPartResult, HashResult } from "../hashing/protocol";
import type {
  PersistedUploadSession,
  UploadAction,
  UploadEffect,
  UploadFailure,
  UploadMachineState,
  UploadPartDescriptor,
  UploadPartState,
  UploadTransition,
} from "./types";

export const initialUploadState: UploadMachineState = {
  phase: "idle",
  generation: 0,
  file: null,
  mediaType: null,
  idempotencyKey: null,
  fileIdentity: null,
  session: null,
  hashMode: null,
  hashProcessedBytes: 0,
  hashResult: null,
  reconciling: false,
  parts: [],
  completion: null,
  failure: null,
};

function accept(state: UploadMachineState, effects: UploadEffect[] = []): UploadTransition {
  return { accepted: true, state, effects };
}

function reject(state: UploadMachineState): UploadTransition {
  return { accepted: false, state, effects: [] };
}

function fail(
  state: UploadMachineState,
  failure: UploadFailure,
  effects: UploadEffect[] = [],
): UploadTransition {
  return accept({ ...state, phase: "failed", failure, reconciling: false }, effects);
}

function isCurrent(state: UploadMachineState, generation: number): boolean {
  return state.generation === generation;
}

function buildParts(parts: readonly HashPartResult[]): UploadPartState[] {
  let startByte = 0;
  return parts.map((part) => {
    const state: UploadPartState = {
      partNumber: part.partNumber,
      startByte,
      endByte: startByte + part.sizeBytes,
      sizeBytes: part.sizeBytes,
      checksumSha256: part.checksumSha256,
      status: "pending",
      attempt: 1,
      uploadedBytes: 0,
      etag: null,
      errorCode: null,
    };
    startByte = state.endByte;
    return state;
  });
}

function matchesServerPartPlan(
  parts: readonly HashPartResult[],
  sizeBytes: number,
  partSizeBytes: number,
): boolean {
  const expectedCount = Math.ceil(sizeBytes / partSizeBytes);
  return (
    parts.length === expectedCount &&
    parts.every((part, index) => {
      const startByte = index * partSizeBytes;
      return (
        part.partNumber === index + 1 &&
        part.sizeBytes === Math.min(partSizeBytes, sizeBytes - startByte)
      );
    })
  );
}

function partDescriptors(parts: readonly UploadPartState[]): UploadPartDescriptor[] {
  return parts
    .filter((part) => part.status === "pending")
    .map(({ partNumber, attempt, startByte, endByte, sizeBytes, checksumSha256 }) => ({
      partNumber,
      attempt,
      startByte,
      endByte,
      sizeBytes,
      checksumSha256,
    }));
}

function queuePartsEffect(state: UploadMachineState, parts: readonly UploadPartState[]): UploadEffect | null {
  if (state.session === null || state.file === null) {
    return null;
  }
  const pending = partDescriptors(parts);
  return pending.length === 0
    ? null
    : {
        type: "queue_parts",
        generation: state.generation,
        sessionId: state.session.sessionId,
        file: state.file,
        parts: pending,
      };
}

function completionEffect(state: UploadMachineState, parts: readonly UploadPartState[]): UploadEffect | null {
  if (state.session === null || parts.some((part) => part.status !== "uploaded" || part.etag === null)) {
    return null;
  }
  return {
    type: "complete_session",
    generation: state.generation,
    sessionId: state.session.sessionId,
    parts: parts.map((part) => ({
      partNumber: part.partNumber,
      sizeBytes: part.sizeBytes,
      etag: part.etag as string,
      checksumSha256: part.checksumSha256,
    })),
  };
}

function replacePart(
  state: UploadMachineState,
  partNumber: number,
  attempt: number,
  update: (part: UploadPartState) => UploadPartState,
): UploadPartState[] | null {
  const index = state.parts.findIndex((part) => part.partNumber === partNumber);
  if (index < 0 || state.parts[index]?.attempt !== attempt) {
    return null;
  }
  const parts = [...state.parts];
  parts[index] = update(parts[index]);
  return parts;
}

function hasPartState(
  state: UploadMachineState,
  partNumber: number,
  attempt: number,
  statuses: readonly UploadPartState["status"][],
): boolean {
  return state.parts.some(
    (part) =>
      part.partNumber === partNumber &&
      part.attempt === attempt &&
      statuses.includes(part.status),
  );
}

function resetActiveParts(parts: readonly UploadPartState[]): UploadPartState[] {
  return parts.map((part) =>
    part.status === "presigning" || part.status === "uploading"
      ? { ...part, status: "pending", uploadedBytes: 0, errorCode: null }
      : part,
  );
}

function persistedSessionFromResponse(response: {
  sessionId: string;
  filename: string;
  sizeBytes: number;
  declaredSha256: string;
  partSizeBytes: number;
  expiresAt: string;
}): PersistedUploadSession {
  return {
    version: 1,
    sessionId: response.sessionId,
    filename: response.filename,
    sizeBytes: response.sizeBytes,
    declaredSha256: response.declaredSha256,
    partSizeBytes: response.partSizeBytes,
    expiresAt: response.expiresAt,
  };
}

function reconcileParts(local: readonly UploadPartState[], uploaded: readonly UploadedPart[]): UploadPartState[] | null {
  const serverByNumber = new Map(uploaded.map((part) => [part.partNumber, part]));
  if (serverByNumber.size !== uploaded.length) {
    return null;
  }
  const reconciled = local.map((part) => {
    const observed = serverByNumber.get(part.partNumber);
    if (observed === undefined) {
      return part;
    }
    if (observed.sizeBytes !== part.sizeBytes || observed.checksumSha256 !== part.checksumSha256) {
      return null;
    }
    return {
      ...part,
      status: "uploaded" as const,
      uploadedBytes: part.sizeBytes,
      etag: observed.etag,
      errorCode: null,
    };
  });
  if (reconciled.some((part) => part === null) || uploaded.some((part) => !local.some((item) => item.partNumber === part.partNumber))) {
    return null;
  }
  return reconciled as UploadPartState[];
}

function handleHashSuccess(state: UploadMachineState, result: HashResult): UploadTransition {
  if (state.file === null || state.hashMode === null) {
    return reject(state);
  }
  if (state.hashMode === "initial") {
    if (state.mediaType === null || state.idempotencyKey === null) {
      return reject(state);
    }
    const fileIdentity = {
      filename: state.file.name,
      sizeBytes: state.file.size,
      declaredSha256: result.wholeSha256,
    };
    return accept(
      { ...state, phase: "creating", fileIdentity, hashResult: null, hashProcessedBytes: state.file.size },
      [
        {
          type: "create_session",
          generation: state.generation,
          request: {
            filename: state.file.name,
            sizeBytes: state.file.size,
            mediaType: state.mediaType,
            sha256: result.wholeSha256,
          },
          idempotencyKey: state.idempotencyKey,
        },
      ],
    );
  }

  if (
    state.fileIdentity === null ||
    state.session === null ||
    compareHashedFileIdentity(state.fileIdentity, {
      filename: state.file.name,
      sizeBytes: state.file.size,
      declaredSha256: result.wholeSha256,
    }) !== null
  ) {
    return fail(state, {
      stage: "file_identity",
      code: "different_file",
      message: "The selected file does not match the upload session.",
      retryable: false,
    });
  }
  if (!matchesServerPartPlan(result.parts, state.fileIdentity.sizeBytes, state.session.partSizeBytes)) {
    return fail(state, {
      stage: "hash",
      code: "invalid_part_plan",
      message: "The browser hash result does not match the server-selected part plan.",
      retryable: true,
    });
  }
  const parts = buildParts(result.parts);
  if (parts.reduce((total, part) => total + part.sizeBytes, 0) !== state.fileIdentity.sizeBytes) {
    return fail(state, {
      stage: "hash",
      code: "invalid_part_plan",
      message: "The browser hash result does not cover the complete file.",
      retryable: true,
    });
  }

  if (state.hashMode === "resume") {
    return accept(
      { ...state, phase: "uploading", hashResult: result, parts, reconciling: true },
      [{ type: "fetch_session", generation: state.generation, sessionId: state.session.sessionId }],
    );
  }

  const nextState = { ...state, phase: "uploading" as const, hashResult: result, parts, reconciling: false };
  const queueEffect = queuePartsEffect(nextState, parts);
  return accept(nextState, queueEffect === null ? [] : [queueEffect]);
}

export function reduceUpload(state: UploadMachineState, action: UploadAction): UploadTransition {
  switch (action.type) {
    case "select_file": {
      if (
        !["idle", "failed", "canceled", "completed"].includes(state.phase) ||
        (state.phase === "failed" && state.session !== null) ||
        action.file.size <= 0
      ) {
        return reject(state);
      }
      const generation = state.generation + 1;
      const nextState: UploadMachineState = {
        ...initialUploadState,
        phase: "hashing",
        generation,
        file: action.file,
        mediaType: action.mediaType,
        idempotencyKey: action.idempotencyKey,
        hashMode: "initial",
      };
      return accept(nextState, [
        { type: "hash_file", generation, mode: "initial", file: action.file, partSizeBytes: action.file.size },
      ]);
    }

    case "restore_session": {
      if (state.phase !== "idle") {
        return reject(state);
      }
      const generation = state.generation + 1;
      return accept({
        ...initialUploadState,
        phase: "awaiting_file",
        generation,
        fileIdentity: {
          filename: action.session.filename,
          sizeBytes: action.session.sizeBytes,
          declaredSha256: action.session.declaredSha256,
        },
        session: action.session,
      });
    }

    case "reselect_file": {
      if (state.phase !== "awaiting_file" || state.fileIdentity === null || state.session === null) {
        return reject(state);
      }
      if (compareFileMetadata(state.fileIdentity, action.file) !== null) {
        return fail(state, {
          stage: "file_identity",
          code: "different_file",
          message: "The selected file name or size does not match the upload session.",
          retryable: false,
        });
      }
      const generation = state.generation + 1;
      return accept(
        {
          ...state,
          phase: "hashing",
          generation,
          file: action.file,
          hashMode: "resume",
          hashProcessedBytes: 0,
          failure: null,
        },
        [
          {
            type: "hash_file",
            generation,
            mode: "resume",
            file: action.file,
            partSizeBytes: state.session.partSizeBytes,
          },
        ],
      );
    }

    case "hash_progress": {
      if (
        state.phase !== "hashing" ||
        !isCurrent(state, action.generation) ||
        !Number.isSafeInteger(action.processedBytes) ||
        !Number.isSafeInteger(action.totalBytes) ||
        action.processedBytes < 0 ||
        action.totalBytes <= 0
      ) {
        return reject(state);
      }
      const bounded = Math.max(0, Math.min(action.processedBytes, action.totalBytes));
      return accept({ ...state, hashProcessedBytes: Math.max(state.hashProcessedBytes, bounded) });
    }

    case "hash_succeeded": {
      if (state.phase !== "hashing" || !isCurrent(state, action.generation)) {
        return reject(state);
      }
      return handleHashSuccess(state, action.result);
    }

    case "hash_failed": {
      if (state.phase !== "hashing" || !isCurrent(state, action.generation)) {
        return reject(state);
      }
      return fail(state, { stage: "hash", code: action.code, message: action.message, retryable: true });
    }

    case "session_created": {
      if (
        !isCurrent(state, action.generation) &&
        state.session?.sessionId !== action.session.sessionId
      ) {
        return accept(state, [{ type: "abort_session", sessionId: action.session.sessionId }]);
      }
      if (state.phase !== "creating" || !isCurrent(state, action.generation) || state.file === null || state.fileIdentity === null) {
        return reject(state);
      }
      if (
        action.session.filename !== state.fileIdentity.filename ||
        action.session.sizeBytes !== state.fileIdentity.sizeBytes ||
        action.session.declaredSha256 !== state.fileIdentity.declaredSha256 ||
        action.session.status !== "active" ||
        action.session.expectedPartCount !== Math.ceil(action.session.sizeBytes / action.session.partSizeBytes)
      ) {
        return fail(
          state,
          {
            stage: "create",
            code: "session_identity_mismatch",
            message: "Created upload session does not match the selected file.",
            retryable: false,
          },
          action.session.status === "active"
            ? [{ type: "abort_session", sessionId: action.session.sessionId }]
            : [],
        );
      }
      const session = persistedSessionFromResponse(action.session);
      return accept(
        {
          ...state,
          phase: "hashing",
          session,
          hashMode: "parts",
          hashProcessedBytes: 0,
          failure: null,
        },
        [
          { type: "persist_session", session },
          {
            type: "hash_file",
            generation: state.generation,
            mode: "parts",
            file: state.file,
            partSizeBytes: session.partSizeBytes,
          },
        ],
      );
    }

    case "session_create_failed": {
      if (state.phase !== "creating" || !isCurrent(state, action.generation)) {
        return reject(state);
      }
      return fail(state, { stage: "create", code: action.code, message: action.message, retryable: true });
    }

    case "session_reconciled": {
      if (state.phase !== "uploading" || !state.reconciling || !isCurrent(state, action.generation) || state.session === null) {
        return reject(state);
      }
      if (
        action.session.sessionId !== state.session.sessionId ||
        action.session.filename !== state.session.filename ||
        action.session.sizeBytes !== state.session.sizeBytes ||
        action.session.declaredSha256 !== state.session.declaredSha256 ||
        action.session.partSizeBytes !== state.session.partSizeBytes ||
        action.session.expectedPartCount !== state.parts.length
      ) {
        return fail(state, {
          stage: "reconcile",
          code: "session_identity_mismatch",
          message: "Server upload session does not match persisted recovery metadata.",
          retryable: false,
        });
      }
      if (action.session.status === "completed") {
        return accept({ ...state, phase: "completed", reconciling: false }, [{ type: "clear_persistence" }]);
      }
      if (action.session.status !== "active") {
        return fail(state, {
          stage: "reconcile",
          code: `session_${action.session.status}`,
          message: "Server upload session is not resumable.",
          retryable: false,
        });
      }
      const parts = reconcileParts(state.parts, action.session.uploadedParts);
      if (parts === null) {
        return fail(state, {
          stage: "reconcile",
          code: "uploaded_part_mismatch",
          message: "Server-observed parts do not match the selected file.",
          retryable: false,
        });
      }
      const completion = completionEffect(state, parts);
      if (completion !== null) {
        return accept({ ...state, phase: "completing", reconciling: false, parts }, [completion]);
      }
      const nextState = { ...state, reconciling: false, parts };
      const queueEffect = queuePartsEffect(nextState, parts);
      return accept(nextState, queueEffect === null ? [] : [queueEffect]);
    }

    case "session_reconcile_failed": {
      if (state.phase !== "uploading" || !state.reconciling || !isCurrent(state, action.generation)) {
        return reject(state);
      }
      return fail(state, { stage: "reconcile", code: action.code, message: action.message, retryable: true });
    }

    case "part_presign_started": {
      if (
        state.phase !== "uploading" ||
        !isCurrent(state, action.generation) ||
        !hasPartState(state, action.partNumber, action.attempt, ["pending"])
      ) {
        return reject(state);
      }
      const parts = replacePart(state, action.partNumber, action.attempt, (part) =>
        ({ ...part, status: "presigning", errorCode: null }),
      );
      return parts === null ? reject(state) : accept({ ...state, parts });
    }

    case "part_upload_started": {
      if (
        state.phase !== "uploading" ||
        !isCurrent(state, action.generation) ||
        !hasPartState(state, action.partNumber, action.attempt, ["presigning"])
      ) {
        return reject(state);
      }
      const parts = replacePart(state, action.partNumber, action.attempt, (part) =>
        ({ ...part, status: "uploading", uploadedBytes: 0 }),
      );
      return parts === null ? reject(state) : accept({ ...state, parts });
    }

    case "part_progress": {
      if (
        state.phase !== "uploading" ||
        !isCurrent(state, action.generation) ||
        !Number.isSafeInteger(action.uploadedBytes) ||
        action.uploadedBytes < 0 ||
        !hasPartState(state, action.partNumber, action.attempt, ["uploading"])
      ) {
        return reject(state);
      }
      const parts = replacePart(state, action.partNumber, action.attempt, (part) =>
        ({ ...part, uploadedBytes: Math.max(part.uploadedBytes, Math.min(action.uploadedBytes, part.sizeBytes)) }),
      );
      return parts === null ? reject(state) : accept({ ...state, parts });
    }

    case "part_uploaded": {
      if (
        state.phase !== "uploading" ||
        !isCurrent(state, action.generation) ||
        action.etag.trim() === "" ||
        !hasPartState(state, action.partNumber, action.attempt, ["uploading"])
      ) {
        return reject(state);
      }
      const parts = replacePart(state, action.partNumber, action.attempt, (part) =>
        ({ ...part, status: "uploaded", uploadedBytes: part.sizeBytes, etag: action.etag, errorCode: null }),
      );
      if (parts === null) {
        return reject(state);
      }
      const completion = completionEffect(state, parts);
      return completion === null
        ? accept({ ...state, parts })
        : accept({ ...state, phase: "completing", parts }, [completion]);
    }

    case "part_failed": {
      if (
        state.phase !== "uploading" ||
        !isCurrent(state, action.generation) ||
        !hasPartState(state, action.partNumber, action.attempt, ["presigning", "uploading"])
      ) {
        return reject(state);
      }
      const parts = replacePart(state, action.partNumber, action.attempt, (part) =>
        ({ ...part, status: "failed", uploadedBytes: 0, etag: null, errorCode: action.code }),
      );
      return parts === null ? reject(state) : accept({ ...state, parts });
    }

    case "pause": {
      if (state.phase !== "uploading" || state.reconciling) {
        return reject(state);
      }
      return accept(
        { ...state, phase: "paused", generation: state.generation + 1, parts: resetActiveParts(state.parts) },
        [
          { type: "pause_scheduler", clearQueued: true },
          { type: "abort_transfers" },
        ],
      );
    }

    case "resume": {
      if (state.phase !== "paused") {
        return reject(state);
      }
      const nextState = { ...state, phase: "uploading" as const };
      const queueEffect = queuePartsEffect(nextState, nextState.parts);
      return accept(nextState, [
        { type: "resume_scheduler" },
        ...(queueEffect === null ? [] : [queueEffect]),
      ]);
    }

    case "retry_part": {
      if (state.phase !== "uploading" && state.phase !== "paused") {
        return reject(state);
      }
      const index = state.parts.findIndex((part) => part.partNumber === action.partNumber && part.status === "failed");
      if (index < 0) {
        return reject(state);
      }
      const parts = [...state.parts];
      const current = parts[index];
      parts[index] = { ...current, status: "pending", attempt: current.attempt + 1, errorCode: null };
      const nextState = { ...state, parts };
      const queueEffect = state.phase === "uploading" ? queuePartsEffect(nextState, [parts[index]]) : null;
      return accept(nextState, queueEffect === null ? [] : [queueEffect]);
    }

    case "retry": {
      if (state.phase !== "failed" || state.failure?.retryable !== true) {
        return reject(state);
      }
      if (state.failure.stage === "hash" && state.file !== null && state.hashMode !== null) {
        return accept(
          { ...state, phase: "hashing", hashProcessedBytes: 0, failure: null },
          [
            {
              type: "hash_file",
              generation: state.generation,
              mode: state.hashMode,
              file: state.file,
              partSizeBytes: state.hashMode === "initial" ? state.file.size : (state.session?.partSizeBytes ?? state.file.size),
            },
          ],
        );
      }
      if (
        state.failure.stage === "create" &&
        state.fileIdentity !== null &&
        state.mediaType !== null &&
        state.idempotencyKey !== null
      ) {
        return accept(
          { ...state, phase: "creating", failure: null },
          [
            {
              type: "create_session",
              generation: state.generation,
              request: {
                filename: state.fileIdentity.filename,
                sizeBytes: state.fileIdentity.sizeBytes,
                mediaType: state.mediaType,
                sha256: state.fileIdentity.declaredSha256,
              },
              idempotencyKey: state.idempotencyKey,
            },
          ],
        );
      }
      if (state.failure.stage === "reconcile" && state.session !== null) {
        return accept(
          { ...state, phase: "uploading", reconciling: true, failure: null },
          [{ type: "fetch_session", generation: state.generation, sessionId: state.session.sessionId }],
        );
      }
      if (state.failure.stage === "complete") {
        const effect = completionEffect(state, state.parts);
        return effect === null
          ? reject(state)
          : accept({ ...state, phase: "completing", failure: null }, [effect]);
      }
      return reject(state);
    }

    case "complete_succeeded": {
      if (
        state.phase !== "completing" ||
        !isCurrent(state, action.generation) ||
        state.session?.sessionId !== action.result.sessionId
      ) {
        return reject(state);
      }
      return accept(
        { ...state, phase: "completed", completion: action.result, failure: null },
        [{ type: "clear_persistence" }],
      );
    }

    case "complete_failed": {
      if (state.phase !== "completing" || !isCurrent(state, action.generation)) {
        return reject(state);
      }
      return fail(state, { stage: "complete", code: action.code, message: action.message, retryable: true });
    }

    case "cancel": {
      if (
        !["awaiting_file", "hashing", "creating", "uploading", "paused", "failed"].includes(state.phase) ||
        (state.phase === "failed" && state.failure?.stage === "complete")
      ) {
        return reject(state);
      }
      const effects: UploadEffect[] = [
        { type: "abort_hash" },
        { type: "abort_transfers" },
        { type: "clear_scheduler" },
        { type: "clear_persistence" },
      ];
      if (state.session !== null) {
        effects.push({ type: "abort_session", sessionId: state.session.sessionId });
      }
      return accept({ ...state, phase: "canceled", generation: state.generation + 1, failure: null }, effects);
    }

    case "clear": {
      if (
        !["completed", "canceled", "failed"].includes(state.phase) ||
        (state.phase === "failed" && state.session !== null)
      ) {
        return reject(state);
      }
      return accept({ ...initialUploadState, generation: state.generation + 1 }, [{ type: "clear_persistence" }]);
    }
  }
}
