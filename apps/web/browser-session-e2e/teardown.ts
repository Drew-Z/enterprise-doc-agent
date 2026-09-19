import { readFile } from "node:fs/promises";
import path from "node:path";
import { setTimeout } from "node:timers/promises";

export default async function teardown() {
  const runDir = process.env.BROWSER_SESSION_OUTPUT_DIR;
  if (!runDir) throw new Error("Missing browser acceptance output directory.");
  const response = await fetch("http://127.0.0.1:18768/test/shutdown", {
    method: "POST", headers: { "X-Browser-Test": "isolated-browser-session" },
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new Error("Browser acceptance shutdown was not acknowledged.");
  const deadline = Date.now() + 40_000;
  while (Date.now() < deadline) {
    let content: string;
    try { content = await readFile(path.join(runDir, "cleanup.json"), "utf8"); }
    catch { await setTimeout(200); continue; }
    const result = JSON.parse(content) as { success: boolean; schemaRemoved: boolean; remainingObjects: number; remainingMultipartUploads: number };
    if (!result.success || !result.schemaRemoved || result.remainingObjects !== 0 || result.remainingMultipartUploads !== 0) {
      throw new Error("Browser acceptance resources remain; inspect cleanup.json.");
    }
    console.log("Browser cleanup: isolated schema removed; 0 objects and multipart uploads remain.");
    return;
  }
  throw new Error("Browser acceptance cleanup did not finish within the deadline.");
}
