import { z } from "zod";

import { sha256HexSchema } from "./api/schemas";
import type { PersistedUploadSession } from "./state/types";

export const UPLOAD_RECOVERY_STORAGE_KEY = "enterprise-doc.upload-recovery.v1";
export const UPLOAD_TOKEN_STORAGE_KEY = "enterprise-doc.upload-token.v1";

export const persistedUploadSessionSchema: z.ZodType<PersistedUploadSession> = z
  .object({
    version: z.literal(1),
    sessionId: z.string().uuid(),
    filename: z.string().min(1),
    sizeBytes: z.number().int().safe().positive(),
    declaredSha256: sha256HexSchema,
    partSizeBytes: z.number().int().safe().positive(),
    expiresAt: z.iso.datetime({ offset: true }),
  })
  .strict();

export class UploadPersistenceError extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "UploadPersistenceError";
  }
}

export interface UploadRecoveryStore {
  load(): PersistedUploadSession | null;
  save(session: PersistedUploadSession): void;
  clear(): void;
}

export interface UploadTokenStore {
  load(): string | null;
  save(token: string): void;
  clear(): void;
}

export function createUploadRecoveryStore(
  storage: Storage,
  key = UPLOAD_RECOVERY_STORAGE_KEY,
): UploadRecoveryStore {
  return {
    load() {
      let raw: string | null;
      try {
        raw = storage.getItem(key);
      } catch (error) {
        throw new UploadPersistenceError("Upload recovery storage could not be read.", { cause: error });
      }
      if (raw === null) {
        return null;
      }
      try {
        const parsed = persistedUploadSessionSchema.safeParse(JSON.parse(raw) as unknown);
        if (parsed.success) {
          return parsed.data;
        }
      } catch {
        // Invalid JSON is treated like any other invalid recovery record.
      }
      try {
        storage.removeItem(key);
      } catch (error) {
        throw new UploadPersistenceError("Invalid upload recovery storage could not be cleared.", { cause: error });
      }
      return null;
    },

    save(session) {
      const parsed = persistedUploadSessionSchema.safeParse(session);
      if (!parsed.success) {
        throw new UploadPersistenceError("Upload recovery record does not match its runtime schema.", {
          cause: parsed.error,
        });
      }
      try {
        storage.setItem(key, JSON.stringify(parsed.data));
      } catch (error) {
        throw new UploadPersistenceError("Upload recovery storage could not be written.", { cause: error });
      }
    },

    clear() {
      try {
        storage.removeItem(key);
      } catch (error) {
        throw new UploadPersistenceError("Upload recovery storage could not be cleared.", { cause: error });
      }
    },
  };
}

export function createUploadTokenStore(storage: Storage, key = UPLOAD_TOKEN_STORAGE_KEY): UploadTokenStore {
  return {
    load() {
      try {
        const token = storage.getItem(key);
        return token === null || token === "" ? null : token;
      } catch (error) {
        throw new UploadPersistenceError("Upload token storage could not be read.", { cause: error });
      }
    },

    save(token) {
      if (token.length === 0 || token.length > 16_384 || /\s/.test(token)) {
        throw new UploadPersistenceError("Upload token is empty, oversized, or contains whitespace.");
      }
      try {
        storage.setItem(key, token);
      } catch (error) {
        throw new UploadPersistenceError("Upload token storage could not be written.", { cause: error });
      }
    },

    clear() {
      try {
        storage.removeItem(key);
      } catch (error) {
        throw new UploadPersistenceError("Upload token storage could not be cleared.", { cause: error });
      }
    },
  };
}
