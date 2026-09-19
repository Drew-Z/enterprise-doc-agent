import { createUploadTokenStore } from "../upload/persistence";
import { captureBrowserCredential, invalidateBrowserCredential, isBrowserAuthentication, type ApiCredential } from "./transport";

export interface ApplicationCredentialStore {
  load(): ApiCredential | null;
  save(token: string): void;
  clear(): void;
}

export function createApplicationCredentialStore(storage: Storage): ApplicationCredentialStore {
  if (!isBrowserAuthentication()) return createUploadTokenStore(storage);
  const credential = captureBrowserCredential();
  return {
    load: () => credential,
    save: () => { throw new Error("Use browser sign-in to establish a session."); },
    clear: () => { if (credential) invalidateBrowserCredential(credential); },
  };
}
