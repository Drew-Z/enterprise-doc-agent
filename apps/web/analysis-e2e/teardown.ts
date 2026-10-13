import { request } from "@playwright/test";
import { writeFileSync } from "node:fs";
import path from "node:path";

export default async function teardown() {
  const client = await request.newContext();
  try {
    const response = await client.post("http://127.0.0.1:18766/__analysis_test__/cleanup", { headers: { "X-Analysis-Test": "analysis-browser" } });
    if (!response.ok()) throw new Error("Isolated browser schema cleanup failed.");
    writeFileSync(path.join(process.env.ANALYSIS_E2E_OUTPUT_DIR!, "cleanup.json"), await response.text());
  } finally { await client.dispose(); }
}
