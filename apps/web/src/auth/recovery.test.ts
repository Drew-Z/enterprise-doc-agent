import { afterEach, beforeEach, expect, it } from "vitest";
import { createAgentRunRecoveryStore } from "../agent/persistence";
import { createUploadRecoveryStore } from "../upload/persistence";
import { activateBrowserCredential, configureAuthentication, retireBrowserCredential } from "./transport";

const identity = { contextVersion: "a".repeat(32) + ".1", csrfToken: "c".repeat(64), tenantId: "tenant-a", actorId: "actor-a" };
const upload = { version: 1 as const, sessionId: "11111111-1111-4111-8111-111111111111", filename: "evidence.txt", sizeBytes: 9, declaredSha256: "a".repeat(64), partSizeBytes: 9, expiresAt: "2099-01-01T00:00:00Z" };
const run = { version: 1 as const, runId: "22222222-2222-4222-8222-222222222222", lastSequence: 3 };

beforeEach(() => { localStorage.clear(); sessionStorage.clear(); configureAuthentication("browser"); });
afterEach(() => { retireBrowserCredential(); configureAuthentication("bearer"); });

it("restores upload and Agent metadata only for the matching enterprise and actor", () => {
  activateBrowserCredential(identity);
  const oldUpload = createUploadRecoveryStore(sessionStorage);
  const oldRun = createAgentRunRecoveryStore(localStorage);
  oldUpload.save(upload);
  oldRun.save(run);
  for (const next of [{ ...identity, tenantId: "tenant-b" }, { ...identity, actorId: "actor-b" }]) {
    activateBrowserCredential(next);
    expect(createUploadRecoveryStore(sessionStorage).load()).toBeNull();
    expect(createAgentRunRecoveryStore(localStorage).load()).toBeNull();
  }
  oldRun.save({ ...run, lastSequence: 4 });
  expect(createAgentRunRecoveryStore(localStorage).load()).toBeNull();
  activateBrowserCredential({ ...identity, contextVersion: "b".repeat(32) + ".2" });
  expect(createUploadRecoveryStore(sessionStorage).load()).toEqual(upload);
  expect(createAgentRunRecoveryStore(localStorage).load()).toEqual({ ...run, lastSequence: 4 });
  const raw = JSON.stringify([Object.values(localStorage), Object.values(sessionStorage)]);
  expect(raw).not.toContain(identity.csrfToken);
  expect(raw).not.toContain(identity.contextVersion);
});

it("does not restore business metadata before an enterprise is selected", () => {
  expect(() => createUploadRecoveryStore(sessionStorage)).toThrow("Select an enterprise");
  expect(() => createAgentRunRecoveryStore(localStorage)).toThrow("Select an enterprise");
});
