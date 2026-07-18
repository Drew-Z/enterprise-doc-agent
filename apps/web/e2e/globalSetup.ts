import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

import { composeFile, rootDirectory, runtimeFile } from "./runtime";

function run(command: string, args: string[]): string {
  return execFileSync(command, args, {
    cwd: rootDirectory,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "inherit"],
  }).trim();
}

export default function globalSetup(): void {
  const runningServices = run("docker", [
    "compose",
    "-f",
    composeFile,
    "ps",
    "--services",
    "--status",
    "running",
  ]);
  const composeOwned = runningServices === "";

  run("docker", ["compose", "-f", composeFile, "up", "-d", "--wait"]);
  run("docker", [
    "compose",
    "-f",
    composeFile,
    "--profile",
    "init",
    "run",
    "--rm",
    "minio-init",
  ]);
  run("uv", ["run", "alembic", "upgrade", "head"]);

  const suffix = Date.now().toString(36);
  const bootstrapOutput = run("uv", [
    "run",
    "python",
    "scripts/bootstrap_local_principal.py",
    "--tenant-name",
    `Playwright M1 ${suffix}`,
    "--tenant-slug",
    `playwright-m1-${suffix}`,
    "--email",
    `playwright-${suffix}@example.test`,
  ]);
  const bootstrap: unknown = JSON.parse(bootstrapOutput);
  if (
    typeof bootstrap !== "object" ||
    bootstrap === null ||
    !("token" in bootstrap) ||
    typeof bootstrap.token !== "string" ||
    bootstrap.token === ""
  ) {
    throw new Error("Local principal bootstrap did not return a token.");
  }

  mkdirSync(path.dirname(runtimeFile), { recursive: true });
  writeFileSync(runtimeFile, JSON.stringify({ token: bootstrap.token, composeOwned }), "utf8");
}
