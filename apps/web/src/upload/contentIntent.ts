import { z } from "zod";
import { scopedRecoveryKey } from "../auth/transport";
import { createUploadRequestSchema } from "./api/schemas";
import { UploadPersistenceError } from "./persistence";

export const CONTENT_INTENT_STORAGE_KEY = "enterprise-doc.upload-content-intent.v1";
export const contentIntentSchema = z.object({
  version: z.literal(1),
  idempotencyKey: z.string().regex(/^[\x21-\x7e]{1,128}$/),
  request: createUploadRequestSchema.extend({
    sizeBytes: z.number().int().positive().max(1_048_576),
    transport: z.literal("single_put"),
  }),
}).strict();
export type ContentUploadIntent = z.infer<typeof contentIntentSchema>;

export function createContentIntentStore(storage: Storage, key = scopedRecoveryKey(CONTENT_INTENT_STORAGE_KEY)) {
  return {
    load(): ContentUploadIntent | null {
      try {
        const raw = storage.getItem(key);
        if (raw === null) return null;
        return contentIntentSchema.parse(JSON.parse(raw) as unknown);
      } catch {
        // An unreadable intent may belong to a request already accepted by the
        // server. Do not silently delete it and allow a new submission.
        throw new UploadPersistenceError("Pending upload recovery could not be read.");
      }
    },
    save(intent: ContentUploadIntent): void {
      try { storage.setItem(key, JSON.stringify(contentIntentSchema.parse(intent))); }
      catch { throw new UploadPersistenceError("Pending upload recovery could not be saved."); }
    },
    clear(): void {
      try { storage.removeItem(key); }
      catch { throw new UploadPersistenceError("Pending upload recovery could not be cleared."); }
    },
  };
}
