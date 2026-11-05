import { request } from "@playwright/test";
import { writeFileSync } from "node:fs";
import path from "node:path";

export default async function teardown() {
  const client = await request.newContext();
  try {
    const response = await client.post("http://127.0.0.1:18765/__workbook_test__/cleanup", { headers: { "X-Workbook-Test": "workbook-browser" } });
    if (!response.ok()) throw new Error(`Owned schema cleanup failed: ${response.status()}`);
    writeFileSync(path.join(process.env.WORKBOOK_E2E_OUTPUT_DIR!, "cleanup.json"), await response.text());
  } finally { await client.dispose(); }
}
