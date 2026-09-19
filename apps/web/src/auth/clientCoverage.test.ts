import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AgentApiClient } from "../agent/api/client";
import { presalesApi } from "../presales/api";
import { fetchAuditEvents, exportAuditEvents } from "../product/auditApi";
import { fetchAuditRetentionPolicy, updateAuditRetentionPolicy, archiveAuditRetentionPlan, fetchAuditArchiveDownload, releaseAuditLegalHold } from "../product/auditGovernanceApi";
import { fetchDocumentInventory, fetchDocumentAccess, updateDocumentAccess, deleteDocumentGrant } from "../product/documentsApi";
import { fetchIdentityBindings, createIdentityBinding, deactivateIdentityBinding } from "../product/identityBindingsApi";
import { fetchTenantMembers, provisionTenantMember, changeTenantMemberRole, deactivateTenantMember } from "../product/membersApi";
import { fetchProductSession } from "../product/sessionApi";
import { fetchTenantUsage } from "../product/tenantUsageApi";
import { UploadApiClient } from "../upload/api/client";
import { activateBrowserCredential, configureAuthentication, type ApiCredential, type Fetcher } from "./transport";

const id = "11111111-1111-4111-8111-111111111111";
const signal = () => new AbortController().signal;
const agent = (credential: ApiCredential) => new AgentApiClient({ getToken: () => credential });
const upload = (credential: ApiCredential) => new UploadApiClient({ getToken: () => credential, allowedObjectStoreOrigins: ["http://objects.example.test"] });
type ClientCase = [string, string, (credential: ApiCredential) => Promise<unknown>];
const cases: ClientCase[] = [
  ["product session", "GET", fetchProductSession],
  ["tenant usage", "GET", credential => fetchTenantUsage(credential, typeof credential === "string" ? id : credential.tenantId)],
  ["document inventory", "GET", fetchDocumentInventory],
  ["document access", "GET", credential => fetchDocumentAccess(credential, id)],
  ["document access update", "PUT", credential => updateDocumentAccess(credential, id, "restricted")],
  ["document grant removal", "DELETE", credential => deleteDocumentGrant(credential, id, id)],
  ["audit events", "GET", fetchAuditEvents],
  ["audit CSV", "GET", exportAuditEvents],
  ["retention policy", "GET", fetchAuditRetentionPolicy],
  ["retention policy update", "PUT", credential => updateAuditRetentionPolicy(credential, { retentionDays: 365, isEnabled: true })],
  ["retention archive", "POST", archiveAuditRetentionPlan],
  ["archive download", "GET", credential => fetchAuditArchiveDownload(credential, id)],
  ["legal hold release", "DELETE", credential => releaseAuditLegalHold(credential, id)],
  ["members", "GET", fetchTenantMembers],
  ["member provisioning", "POST", credential => provisionTenantMember(credential, "member@example.test", "member")],
  ["member role", "PUT", credential => changeTenantMemberRole(credential, id, "member")],
  ["member deactivation", "DELETE", credential => deactivateTenantMember(credential, id)],
  ["identity bindings", "GET", fetchIdentityBindings],
  ["binding creation", "POST", credential => createIdentityBinding(credential, { issuer: "https://id.example.test", subject: "test-subject", userId: id })],
  ["binding deactivation", "DELETE", credential => deactivateIdentityBinding(credential, id)],
  ["Agent source list", "GET", credential => agent(credential).listReadyDocumentVersions()],
  ["Agent events", "GET", credential => agent(credential).listEvents(id)],
  ["Agent SSE", "GET", credential => agent(credential).openEventStream(id, 0)],
  ["Agent cancellation", "POST", credential => agent(credential).cancelRun(id)],
  ["Agent artifact download", "GET", credential => agent(credential).getArtifactDownload(id)],
  ["upload create", "POST", credential => upload(credential).createSession({ filename: "file.txt", sizeBytes: 1, mediaType: "text/plain", sha256: "a".repeat(64) }, id)],
  ["upload read", "GET", credential => upload(credential).getSession(id)],
  ["upload part signing", "POST", credential => upload(credential).presignPart(id, 1, { sizeBytes: 1, checksumSha256: "A".repeat(43) + "=" })],
  ["upload abort", "DELETE", credential => upload(credential).abortSession(id)],
  ["presales list", "GET", credential => presalesApi(credential).list(signal())],
  ["presales generation", "POST", credential => presalesApi(credential).generate(id, id, id, signal())],
  ["presales review", "PUT", credential => presalesApi(credential).review(id, id, { status: "insufficient_evidence", answer: "Further evidence required.", conditions: [], missingInformation: [], expectedRevision: 1, note: "" }, id, signal())],
  ["presales CSV", "GET", credential => presalesApi(credential).export(id, "reviewed", signal())],
];

beforeEach(() => { configureAuthentication("browser"); vi.stubEnv("VITE_API_BASE_URL", ""); });
afterEach(() => { configureAuthentication("bearer"); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

it.each(cases)("uses the browser transport for %s", async (_name, method, invoke) => {
  const credential = activateBrowserCredential({ contextVersion: "a".repeat(32) + ".1", csrfToken: "c".repeat(64), tenantId: "tenant", actorId: "actor" });
  const fetcher = vi.fn<Fetcher>(() => Promise.resolve(new Response("{}", { status: 503 })));
  vi.stubGlobal("fetch", fetcher);
  await expect(invoke(credential)).rejects.toBeInstanceOf(Error);
  expect(fetcher).toHaveBeenCalledOnce();
  const [url, request] = fetcher.mock.calls[0];
  expect(url).toEqual(expect.stringMatching(/^\/api\//));
  expect(request).toMatchObject({ credentials: "same-origin", cache: "no-store", redirect: "error" });
  expect(request?.method ?? "GET").toBe(method);
  const headers = new Headers(request?.headers);
  expect(headers.get("X-Session-Context")).toBe(credential.contextVersion);
  expect(headers.get("X-CSRF-Token")).toBe(method === "GET" ? null : credential.csrfToken);
  expect(headers.has("Authorization")).toBe(false);
  expect(request?.signal?.aborted).toBe(false);
});
