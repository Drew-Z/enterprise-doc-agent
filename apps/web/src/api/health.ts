export type ComponentStatus = "up" | "down" | "timeout";

export type ComponentName = "database" | "redis" | "object_store";

export interface ComponentHealth {
  status: ComponentStatus;
}

export interface ReadinessResponse {
  status: "ready" | "not_ready";
  checks: Record<ComponentName, ComponentHealth>;
  /** Server-side probe time; optional for backwards-compatible older APIs. */
  checkedAt?: string;
}

interface ReadinessResponseWire {
  status: ReadinessResponse["status"];
  checks: Record<ComponentName, ComponentHealth>;
  checked_at?: string;
}

const componentNames: ComponentName[] = ["database", "redis", "object_store"];
const componentStatuses: ComponentStatus[] = ["up", "down", "timeout"];

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function isReadinessResponse(value: unknown): value is ReadinessResponseWire {
  if (!isObject(value) || (value.status !== "ready" && value.status !== "not_ready")) {
    return false;
  }
  if (!isObject(value.checks)) {
    return false;
  }
  const checks = value.checks;
  if (value.checked_at !== undefined && typeof value.checked_at !== "string") {
    return false;
  }
  return componentNames.every((name) => {
    const component = checks[name];
    return isObject(component) && componentStatuses.includes(component.status as ComponentStatus);
  }) && (value.checked_at === undefined || !Number.isNaN(Date.parse(value.checked_at)));
}

function normalizeReadiness(body: ReadinessResponseWire): ReadinessResponse {
  const normalized: ReadinessResponse = {
    status: body.status,
    checks: body.checks,
  };
  if (body.checked_at !== undefined) normalized.checkedAt = body.checked_at;
  return normalized;
}

export async function fetchReadiness(): Promise<ReadinessResponse> {
  const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "";
  const response = await fetch(`${baseUrl}/health/ready`, {
    headers: { Accept: "application/json" },
  });
  const body = (await response.json()) as unknown;

  if (!isReadinessResponse(body)) {
    throw new Error("Readiness response schema is invalid");
  }
  if (response.status === 200 && body.status === "ready") {
    return normalizeReadiness(body);
  }
  if (response.status === 503 && body.status === "not_ready") {
    return normalizeReadiness(body);
  }
  throw new Error(`Unexpected readiness response: ${response.status}`);
}
