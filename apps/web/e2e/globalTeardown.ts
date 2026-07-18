import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, rmSync } from "node:fs";

import { composeFile, parseRuntime, rootDirectory, runtimeFile } from "./runtime";

export default function globalTeardown(): void {
  if (!existsSync(runtimeFile)) {
    return;
  }
  const runtime = parseRuntime(JSON.parse(readFileSync(runtimeFile, "utf8")) as unknown);
  rmSync(runtimeFile, { force: true });
  if (!runtime.composeOwned) {
    return;
  }
  execFileSync("docker", ["compose", "-f", composeFile, "down"], {
    cwd: rootDirectory,
    stdio: "inherit",
  });
}
