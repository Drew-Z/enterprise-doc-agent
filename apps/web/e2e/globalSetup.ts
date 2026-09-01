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
  const tenantSlug = `playwright-m1-${suffix}`;
  const ownerBootstrapOutput = run("uv", [
    "run",
    "python",
    "scripts/bootstrap_local_principal.py",
    "--tenant-name",
    `Playwright M1 ${suffix}`,
    "--tenant-slug",
    tenantSlug,
    "--email",
    `playwright-${suffix}@example.test`,
  ]);
  const memberBootstrapOutput = run("uv", [
    "run",
    "python",
    "scripts/bootstrap_local_principal.py",
    "--tenant-name",
    `Playwright M1 ${suffix}`,
    "--tenant-slug",
    tenantSlug,
    "--email",
    `playwright-member-${suffix}@example.test`,
    "--role",
    "member",
  ]);
  const ownerBootstrap: unknown = JSON.parse(ownerBootstrapOutput);
  const memberBootstrap: unknown = JSON.parse(memberBootstrapOutput);
  if (
    typeof ownerBootstrap !== "object" ||
    ownerBootstrap === null ||
    !("token" in ownerBootstrap) ||
    typeof ownerBootstrap.token !== "string" ||
    ownerBootstrap.token === "" ||
    typeof memberBootstrap !== "object" ||
    memberBootstrap === null ||
    !("token" in memberBootstrap) ||
    typeof memberBootstrap.token !== "string" ||
    memberBootstrap.token === "" ||
    !("actorId" in memberBootstrap) ||
    typeof memberBootstrap.actorId !== "string" ||
    memberBootstrap.actorId === ""
  ) {
    throw new Error("Local owner/member bootstrap did not return valid tokens.");
  }

  mkdirSync(path.dirname(runtimeFile), { recursive: true });
  writeFileSync(
    runtimeFile,
    JSON.stringify({
      token: ownerBootstrap.token,
      memberToken: memberBootstrap.token,
      memberActorId: memberBootstrap.actorId,
      composeOwned,
    }),
    "utf8",
  );
}
