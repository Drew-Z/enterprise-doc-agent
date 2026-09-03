import path from "node:path";

export const rootDirectory = path.resolve(import.meta.dirname, "../../..");
export const composeFile = path.join(rootDirectory, "infra", "compose", "docker-compose.yml");
export const runtimeFile = path.join(rootDirectory, "tmp", "playwright-m1-runtime.json");

export interface PlaywrightRuntime {
  token: string;
  memberToken: string;
  memberActorId: string;
  composeOwned: boolean;
}

export function parseRuntime(value: unknown): PlaywrightRuntime {
  if (
    typeof value !== "object" ||
    value === null ||
    !("token" in value) ||
    typeof value.token !== "string" ||
    value.token === "" ||
    !("memberToken" in value) ||
    typeof value.memberToken !== "string" ||
    value.memberToken === "" ||
    !("memberActorId" in value) ||
    typeof value.memberActorId !== "string" ||
    value.memberActorId === "" ||
    !("composeOwned" in value) ||
    typeof value.composeOwned !== "boolean"
  ) {
    throw new Error("Playwright runtime state is invalid.");
  }
  return {
    token: value.token,
    memberToken: value.memberToken,
    memberActorId: value.memberActorId,
    composeOwned: value.composeOwned,
  };
}
