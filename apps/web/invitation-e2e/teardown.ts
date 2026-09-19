import { readFile } from "node:fs/promises";
import path from "node:path";
import { setTimeout } from "node:timers/promises";
import { z } from "zod";

export default async function teardown() {
  const runDir = process.env.INVITATION_OUTPUT_DIR;
  if (!runDir) throw new Error("Missing invitation acceptance directory.");
  const run = z.object({ runId: z.string().regex(/^[0-9a-f]{32}$/) }).parse(JSON.parse(await readFile(path.join(runDir, "run-context.json"), "utf8")));
  const response = await fetch("http://127.0.0.1:18770/test/shutdown", {
    method: "POST", headers: { "X-Invitation-Run": run.runId }, signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new Error("Invitation acceptance shutdown was not acknowledged.");
  const reply = z.object({ runId: z.string(), stopping: z.literal(true) }).parse(await response.json());
  if (reply.runId !== run.runId) throw new Error("Unexpected invitation acceptance process.");
  const deadline = Date.now() + 40_000;
  while (Date.now() < deadline) {
    let content: string;
    try { content = await readFile(path.join(runDir, "cleanup.json"), "utf8"); }
    catch { await setTimeout(200); continue; }
    z.object({ runId: z.literal(run.runId), success: z.literal(true), schemaRemoved: z.literal(true),
      objectWrites: z.literal(0), workerStarts: z.literal(0), externalModelRequests: z.literal(0), emailsSent: z.literal(0),
    }).parse(JSON.parse(content));
    console.log("Invitation cleanup: isolated schema removed; no uploads, worker starts, model requests or email delivery.");
    return;
  }
  throw new Error("Invitation acceptance cleanup did not finish within the deadline.");
}
