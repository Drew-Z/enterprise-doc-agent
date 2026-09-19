import {
  admissionInputSchema, admissionReceiptSchema, browserAuthenticatedSchema, browserLogoutSchema,
  browserSessionSchema, browserTenantsSchema, type BrowserAuthenticatedSession, type BrowserTenant,
} from "./schemas";
import { activateBrowserCredential, onBrowserCredentialInvalidated, retireBrowserCredential, type Fetcher } from "./transport";
import { errorResponseSchema } from "../agent/api/schemas";
import { invitationInputSchema, invitationPreviewSchema, invitationReceiptSchema, type InvitationPreview } from "../invitations/schemas";

export type BrowserNotice = "expired" | "session_changed" | "service_unavailable" | "selection_unconfirmed" | "admission_failed" | "admission_complete" | "signed_out" | "sign_in_failed"
  | "invitation_failed" | "invitation_complete" | "invitation_full" | "invitation_account_conflict" | "invitation_conflict" | "invitation_unavailable" | "invitation_unconfirmed";
export interface BrowserSessionState {
  phase: "loading" | "anonymous" | "disabled" | "unavailable" | "choosing" | "working" | "busy" | "logout-unconfirmed";
  session: BrowserAuthenticatedSession | null;
  tenants: BrowserTenant[];
  notice: BrowserNotice | null;
  workspaceKey: number;
  verifying: boolean;
}

interface Dependencies {
  clearWorkspace(): void;
  broadcast(): void;
  fetcher?: Fetcher;
}

interface Operation { id: number; signal: AbortSignal }

class BrowserRequestError extends Error {
  constructor(readonly status: number, readonly code: string) {
    super("The browser session request could not be completed.");
  }
}

export class BrowserSessionController {
  private state: BrowserSessionState = { phase: "loading", session: null, tenants: [], notice: null, workspaceKey: 0, verifying: false };
  private listeners = new Set<() => void>();
  private session: BrowserAuthenticatedSession | null = null;
  private logoutSnapshot: BrowserAuthenticatedSession | null = null;
  private logoutUnconfirmed = false;
  private sequence = 0;
  private control: AbortController | null = null;
  private expiryTimer: ReturnType<typeof setTimeout> | null = null;
  private unsubscribe: (() => void) | null = null;

  constructor(private readonly dependencies: Dependencies) {}

  getSnapshot = (): BrowserSessionState => this.state;
  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };

  start(): void {
    this.unsubscribe?.();
    this.unsubscribe = onBrowserCredentialInvalidated(() => { void this.reconcile("session_changed"); });
    void this.reconcile();
  }

  stop(): void {
    this.unsubscribe?.();
    this.unsubscribe = null;
    this.sequence += 1;
    this.control?.abort();
    this.clearWorkspace();
  }

  private publish(patch: Partial<BrowserSessionState>): void {
    this.state = { ...this.state, ...patch };
    for (const listener of this.listeners) listener();
  }

  private clearWorkspace(): void {
    retireBrowserCredential();
    if (this.expiryTimer !== null) clearTimeout(this.expiryTimer);
    this.expiryTimer = null;
    this.dependencies.clearWorkspace();
  }

  private begin(phase: BrowserSessionState["phase"], notice: BrowserNotice | null = null, preserveWorkspace = false): Operation {
    this.sequence += 1;
    this.control?.abort();
    this.control = new AbortController();
    if (!preserveWorkspace) this.clearWorkspace();
    this.publish({ phase: preserveWorkspace ? "working" : phase, notice, session: this.session, tenants: [], verifying: preserveWorkspace });
    return { id: this.sequence, signal: this.control.signal };
  }

  private current(operation: Operation): boolean {
    return operation.id === this.sequence && !operation.signal.aborted;
  }

  private async request(path: string, operation: Operation, session: BrowserAuthenticatedSession | null = null, payload?: unknown): Promise<unknown> {
    const headers = new Headers({ Accept: "application/json" });
    if (session) headers.set("X-Session-Context", session.contextVersion);
    if (payload !== undefined) {
      headers.set("Content-Type", "application/json");
      if (session) headers.set("X-CSRF-Token", session.csrfToken);
    }
    const fetcher = this.dependencies.fetcher ?? fetch;
    const response = await fetcher(path, {
      method: payload === undefined ? "GET" : "POST", headers,
      body: payload === undefined ? undefined : JSON.stringify(payload),
      credentials: "same-origin", cache: "no-store", redirect: "error",
      signal: AbortSignal.any([operation.signal, AbortSignal.timeout(20_000)]),
    });
    if (!this.current(operation)) throw new DOMException("Session changed", "AbortError");
    if (!response.ok) {
      const parsed = errorResponseSchema.safeParse(await response.json().catch(() => null));
      if (!this.current(operation)) throw new DOMException("Session changed", "AbortError");
      throw new BrowserRequestError(response.status, parsed.success ? parsed.data.error.code : "browser_request_failed");
    }
    const body: unknown = await response.json();
    if (!this.current(operation)) throw new DOMException("Session changed", "AbortError");
    return body;
  }

  private install(session: BrowserAuthenticatedSession): void {
    this.session = session;
    const tenant = session.currentTenant;
    if (!tenant) throw new Error("An enterprise must be selected.");
    const remaining = Date.parse(session.expiresAt) - Date.now();
    if (remaining <= 0) { this.expire(); return; }
    activateBrowserCredential({ contextVersion: session.contextVersion, csrfToken: session.csrfToken, tenantId: tenant.tenantId, actorId: tenant.actorId });
    this.publish({ phase: "working", session, tenants: [], notice: null, workspaceKey: this.state.workspaceKey + 1, verifying: false });
    this.expiryTimer = setTimeout(() => this.expire(), Math.min(remaining, 2_147_483_647));
  }

  private expire(): void {
    this.session = null;
    this.begin("anonymous", "expired");
  }

  async reconcile(notice: BrowserNotice | null = null): Promise<void> {
    if (this.logoutUnconfirmed) return;
    const previous = this.session;
    const preserveWorkspace = notice === null && this.state.phase === "working";
    const operation = this.begin("loading", notice, preserveWorkspace);
    try {
      const session = browserSessionSchema.parse(await this.request("/auth/session", operation));
      if (!this.current(operation)) return;
      if (session.status !== "authenticated") {
        if (preserveWorkspace) this.clearWorkspace();
        this.session = null;
        this.publish({ phase: session.status, session: null, verifying: false });
        return;
      }
      // Both snapshots passed the same strict schema, so serialization compares the
      // complete identity, selection, role, expiry, context and CSRF contract.
      if (preserveWorkspace && JSON.stringify(previous) === JSON.stringify(session)) {
        this.session = session;
        this.publish({ session, verifying: false });
        return;
      }
      if (preserveWorkspace) this.clearWorkspace();
      this.session = session;
      if (session.currentTenant) this.install(session);
      else await this.loadTenants(operation, session);
    } catch {
      if (this.current(operation)) {
        if (preserveWorkspace) this.clearWorkspace();
        this.publish({ phase: "unavailable", notice: "service_unavailable", verifying: false });
      }
    }
  }

  suspend(): void {
    if (this.logoutUnconfirmed) return;
    this.begin("loading", "session_changed");
  }

  private async loadTenants(operation: Operation, session: BrowserAuthenticatedSession, notice: BrowserNotice | null = null): Promise<void> {
    const tenants = browserTenantsSchema.parse(await this.request("/auth/tenants", operation, session));
    if (this.current(operation)) this.publish({ phase: "choosing", session, tenants, notice });
  }

  async showTenants(): Promise<void> {
    const session = this.session;
    if (!session || this.logoutUnconfirmed) return;
    const operation = this.begin("loading");
    try { await this.loadTenants(operation, session); }
    catch { if (this.current(operation)) this.publish({ phase: "unavailable", notice: "service_unavailable" }); }
  }

  async selectTenant(tenantId: string): Promise<void> {
    const session = this.session;
    if (!session || this.state.phase !== "choosing" || !this.state.tenants.some(tenant => tenant.tenantId === tenantId)) return;
    const operation = this.begin("busy");
    try {
      const selected = browserAuthenticatedSchema.parse(await this.request("/auth/tenant", operation, session, { tenantId }));
      if (!this.current(operation)) return;
      if (selected.currentTenant?.tenantId !== tenantId) throw new Error("Unexpected enterprise selection.");
      this.install(selected);
      this.dependencies.broadcast();
    } catch {
      if (this.current(operation)) this.publish({ phase: "unavailable", notice: "selection_unconfirmed" });
    }
  }

  async acceptAdmission(token: string, tenantName: string): Promise<boolean> {
    const session = this.session;
    if (!session || this.state.phase !== "choosing") return false;
    const input = admissionInputSchema.safeParse({ token, tenantName });
    if (!input.success) { this.publish({ notice: "admission_failed" }); return false; }
    const operation = this.begin("busy");
    let accepted = false;
    try {
      admissionReceiptSchema.parse(await this.request("/auth/admission/accept", operation, session, input.data));
      if (!this.current(operation)) return false;
      accepted = true;
      this.dependencies.broadcast();
      await this.loadTenants(operation, session, "admission_complete");
    } catch {
      if (this.current(operation)) this.publish({ phase: accepted ? "unavailable" : "choosing", session, notice: accepted ? "admission_complete" : "admission_failed" });
    }
    return accepted && this.current(operation);
  }

  async inspectInvitation(token: string): Promise<InvitationPreview | null> {
    const session = this.session;
    if (!session || !["choosing", "working"].includes(this.state.phase) || this.logoutUnconfirmed) return null;
    const input = invitationInputSchema.safeParse({ token });
    if (!input.success) { this.publish({ notice: "invitation_failed" }); return null; }
    const operation = this.begin("busy");
    try {
      const preview = invitationPreviewSchema.parse(await this.request("/auth/invitations/inspect", operation, session, input.data));
      if (!this.current(operation)) return null;
      this.publish({ phase: "choosing", session, notice: null });
      return preview;
    } catch (error) {
      if (this.current(operation)) this.invitationFailure(error, false);
      return null;
    }
  }

  async acceptInvitation(token: string): Promise<boolean> {
    const session = this.session;
    if (!session || this.state.phase !== "choosing" || this.logoutUnconfirmed) return false;
    const input = invitationInputSchema.safeParse({ token });
    if (!input.success) { this.publish({ notice: "invitation_failed" }); return false; }
    const operation = this.begin("busy");
    let accepted = false;
    try {
      invitationReceiptSchema.parse(await this.request("/auth/invitations/accept", operation, session, input.data));
      if (!this.current(operation)) return false;
      accepted = true;
      this.dependencies.broadcast();
      await this.loadTenants(operation, session, "invitation_complete");
    } catch (error) {
      if (this.current(operation)) {
        if (accepted) this.publish({ phase: "unavailable", notice: "invitation_complete" });
        else this.invitationFailure(error, true);
      }
    }
    return accepted && this.current(operation);
  }

  private invitationFailure(error: unknown, writing: boolean): void {
    if (error instanceof BrowserRequestError && error.status === 401) { this.expire(); return; }
    if (error instanceof BrowserRequestError && ["browser_context_stale", "browser_principal_forbidden", "browser_identity_conflict"].includes(error.code)) {
      void this.reconcile("session_changed");
      return;
    }
    const notices: Record<string, BrowserNotice> = {
      membership_seat_limit_reached: "invitation_full",
      invitation_account_conflict: "invitation_account_conflict",
      invitation_conflict: "invitation_conflict",
      invitation_busy: "invitation_conflict",
      invitations_unavailable: "invitation_unavailable",
      invitation_entitlement_required: "invitation_unavailable",
      invitation_denied: "invitation_failed",
    };
    const notice = error instanceof BrowserRequestError
      ? notices[error.code] ?? (writing ? "invitation_unconfirmed" : "invitation_failed")
      : writing ? "invitation_unconfirmed" : "invitation_failed";
    this.publish({ phase: "choosing", notice });
  }

  async logout(): Promise<void> {
    const session = this.logoutSnapshot ?? this.session;
    if (!session) return;
    this.logoutSnapshot = session;
    this.logoutUnconfirmed = true;
    this.session = null;
    const operation = this.begin("busy");
    try {
      browserLogoutSchema.parse(await this.request("/auth/logout", operation, session, {}));
      if (!this.current(operation)) return;
      this.logoutSnapshot = null;
      this.logoutUnconfirmed = false;
      this.publish({ phase: "anonymous", session: null, notice: "signed_out" });
      this.dependencies.broadcast();
    } catch {
      if (this.current(operation)) this.publish({ phase: "logout-unconfirmed", session: null });
    }
  }

  async verifyAfterLogout(): Promise<void> {
    this.logoutUnconfirmed = false;
    this.logoutSnapshot = null;
    await this.reconcile();
  }
}
