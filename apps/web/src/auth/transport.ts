export type AuthenticationMode = "browser" | "bearer";
export type Fetcher = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

export interface BrowserCredential {
  readonly kind: "browser";
  readonly contextVersion: string;
  readonly csrfToken: string;
  readonly tenantId: string;
  readonly actorId: string;
  readonly signal: AbortSignal;
}

export type ApiCredential = string | BrowserCredential;

let mode: AuthenticationMode = "browser";
let active: BrowserCredential | null = null;
let controller: AbortController | null = null;
const invalidationListeners = new Set<() => void>();

export function configureAuthentication(next: AuthenticationMode): void {
  retireBrowserCredential();
  mode = next;
}

export function isBrowserAuthentication(): boolean {
  return mode === "browser";
}

export function captureBrowserCredential(): BrowserCredential | null {
  return active;
}

export function activateBrowserCredential(context: Omit<BrowserCredential, "kind" | "signal">): BrowserCredential {
  if (mode !== "browser") throw new Error("Browser authentication is not enabled.");
  retireBrowserCredential();
  controller = new AbortController();
  active = Object.freeze({ ...context, kind: "browser", signal: controller.signal });
  return active;
}

export function retireBrowserCredential(): void {
  controller?.abort();
  controller = null;
  active = null;
}

export function onBrowserCredentialInvalidated(listener: () => void): () => void {
  invalidationListeners.add(listener);
  return () => { invalidationListeners.delete(listener); };
}

export function invalidateBrowserCredential(credential: BrowserCredential): void {
  if (active !== credential) return;
  retireBrowserCredential();
  for (const listener of invalidationListeners) listener();
}

function assertCurrent(credential: BrowserCredential): void {
  if (mode !== "browser" || active !== credential || credential.signal.aborted) {
    throw new DOMException("The enterprise session has changed.", "AbortError");
  }
}

export function scopedRecoveryKey(key: string): string {
  if (mode === "bearer") return key;
  if (active === null) throw new Error("Select an enterprise before restoring work.");
  return `${key}:${active.tenantId}:${active.actorId}`;
}

const invalidSessionCodes = new Set([
  "browser_context_stale", "browser_principal_forbidden", "browser_identity_conflict",
]);

/** A credential is captured by the caller; an old operation never adopts a newer context. */
export async function authenticatedFetch(
  url: string,
  credential: ApiCredential,
  init: RequestInit = {},
  fetcher: Fetcher = fetch,
): Promise<Response> {
  const headers = new Headers(init.headers);
  if (["Authorization", "Cookie", "X-Session-Context", "X-CSRF-Token"].some(name => headers.has(name))) {
    throw new Error("Authentication headers belong to the application transport.");
  }
  const request: RequestInit = { ...init, headers, cache: "no-store", redirect: "error" };
  if (typeof credential === "string") {
    if (mode !== "bearer" || !credential || /\s/.test(credential)) throw new Error("A valid session is required.");
    headers.set("Authorization", `Bearer ${credential}`);
    request.credentials = "omit";
    return fetcher(url, request);
  }
  assertCurrent(credential);
  const parsed = new URL(url, window.location.origin);
  if (parsed.origin !== window.location.origin || !parsed.pathname.startsWith("/api/") || parsed.username || parsed.password || parsed.hash) {
    throw new Error("Browser business requests must use the application origin.");
  }
  headers.set("X-Session-Context", credential.contextVersion);
  if (!["GET", "HEAD", "OPTIONS"].includes((init.method ?? "GET").toUpperCase())) {
    headers.set("X-CSRF-Token", credential.csrfToken);
  }
  request.credentials = "same-origin";
  request.signal = init.signal ? AbortSignal.any([credential.signal, init.signal]) : credential.signal;
  const response = await fetcher(url, request);
  assertCurrent(credential);
  if (response.status === 401 || response.status === 403 || response.status === 409) {
    const body: unknown = await response.clone().json().catch(() => null);
    const code = typeof body === "object" && body !== null && "error" in body
      && typeof body.error === "object" && body.error !== null && "code" in body.error
      ? body.error.code : null;
    if (response.status === 401 || (typeof code === "string" && invalidSessionCodes.has(code))) {
      invalidateBrowserCredential(credential);
    }
    assertCurrent(credential);
  }
  return response;
}
