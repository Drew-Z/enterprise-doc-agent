export interface ApiErrorMetadata {
  code: string | null;
  requestId: string | null;
}

function readMetadata(error: unknown): ApiErrorMetadata {
  if (typeof error !== "object" || error === null) {
    return { code: null, requestId: null };
  }
  const candidate = error as { code?: unknown; requestId?: unknown };
  return {
    code: typeof candidate.code === "string" && candidate.code !== "" ? candidate.code : null,
    requestId: typeof candidate.requestId === "string" && candidate.requestId !== "" ? candidate.requestId : null,
  };
}

export function formatApiError(error: unknown, fallback: string, requestIdLabel: string): string {
  const message = error instanceof Error && error.message !== ""
    ? error.message
    : typeof error === "object" && error !== null && "message" in error && typeof error.message === "string" && error.message !== ""
      ? error.message
      : fallback;
  const metadata = readMetadata(error);
  const details = [metadata.code, metadata.requestId ? `${requestIdLabel}: ${metadata.requestId}` : null].filter(
    (value): value is string => value !== null,
  );
  return details.length > 0 ? `${message} (${details.join(" · ")})` : message;
}
