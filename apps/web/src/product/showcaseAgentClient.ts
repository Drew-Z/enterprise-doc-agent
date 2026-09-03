import type { AgentWorkspaceDependencies } from "../agent/AgentWorkspace";
import type { AgentApiClientProtocol } from "../agent/api/client";
import {
  showcaseArtifactId,
  showcaseArtifacts,
  showcaseEvents,
  showcasePreview,
  showcaseReadyDocuments,
  showcaseRun,
  showcaseRunId,
} from "./showcaseData";

function unavailable(): Promise<never> {
  return Promise.reject(new Error("This action is unavailable in the read-only showcase snapshot."));
}

const showcaseAgentClient: AgentApiClientProtocol = {
  listReadyDocumentVersions: () => Promise.resolve(showcaseReadyDocuments),
  createRun: () => unavailable(),
  getRun: (requestedRunId) => (
    requestedRunId === showcaseRunId
      ? Promise.resolve(showcaseRun)
      : unavailable()
  ),
  listEvents: (requestedRunId, afterSequence = 0) => (
    requestedRunId === showcaseRunId
      ? Promise.resolve(showcaseEvents.filter((event) => event.seq > afterSequence))
      : unavailable()
  ),
  openEventStream: () => unavailable(),
  cancelRun: () => unavailable(),
  getApproval: () => unavailable(),
  decideApproval: () => unavailable(),
  listArtifacts: (requestedRunId) => (
    requestedRunId === showcaseRunId
      ? Promise.resolve(showcaseArtifacts)
      : unavailable()
  ),
  getArtifactPreview: (requestedArtifactId) => (
    requestedArtifactId === showcaseArtifactId
      ? Promise.resolve(showcasePreview)
      : unavailable()
  ),
  getArtifactDownload: () => unavailable(),
};

export const showcaseAgentWorkspaceDependencies: AgentWorkspaceDependencies = {
  createApiClient: () => showcaseAgentClient,
  idempotencyKeyFactory: () => "showcase-read-only",
  openExternal: () => undefined,
};
