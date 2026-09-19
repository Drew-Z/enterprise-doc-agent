import { readFile } from "node:fs/promises";
import path from "node:path";
import { setTimeout } from "node:timers/promises";
import { z } from "zod";

export default async function teardown() {
  const runDir = process.env.FIRST_USE_OUTPUT_DIR;
  if (!runDir) throw new Error("Missing first-use output directory.");
  const run = z.object({ runId: z.string().regex(/^[0-9a-f]{32}$/), identityProvider: z.enum(["signed", "keycloak"]).default("signed") }).parse(
    JSON.parse(await readFile(path.join(runDir, "run-context.json"), "utf8")),
  );
  const response = await fetch("http://127.0.0.1:18770/test/shutdown", {
    method: "POST",
    headers: { "X-Invitation-Run": run.runId },
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new Error("First-use shutdown was not acknowledged.");
  const reply = z.object({ runId: z.literal(run.runId), stopping: z.literal(true) }).parse(await response.json());
  if (!reply.stopping) throw new Error("First-use server did not begin shutdown.");
  const deadline = Date.now() + 50_000;
  while (Date.now() < deadline) {
    let content: string;
    try {
      content = await readFile(path.join(runDir, "cleanup.json"), "utf8");
    } catch {
      await setTimeout(200);
      continue;
    }
    z.object({
      runId: z.literal(run.runId),
      success: z.literal(true),
      schemaRemoved: z.literal(true),
      workerStopped: z.literal(true),
      publisherStopped: z.literal(true),
      publicUnchanged: z.literal(true),
      remainingObjects: z.literal(0),
      remainingMultipartUploads: z.literal(0),
      remainingRedisKeys: z.literal(0),
      externalModelRequests: z.literal(0),
      emailsSent: z.literal(0),
    }).parse(JSON.parse(content));
    if (run.identityProvider === "keycloak") {
      let identityCleanup: string;
      try {
        identityCleanup = await readFile(path.join(runDir, "keycloak-cleanup.json"), "utf8");
      } catch {
        await setTimeout(200);
        continue;
      }
      z.object({ status: z.literal("passed"), containersRemoved: z.literal(true), networksRemoved: z.literal(true), resourcesRemoved: z.literal(true), internetEmailsSent: z.literal(0) }).parse(JSON.parse(identityCleanup));
      console.log("Real Keycloak cleanup verified: owned containers and network removed; no Internet email sent.");
    }
    console.log("First-use cleanup verified: worker stopped; owned schema, objects and Redis keys removed; public rows unchanged.");
    return;
  }
  throw new Error("First-use cleanup did not complete within 50 seconds.");
}
