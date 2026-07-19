import type { PersistedAgentRun } from "./api/schemas";
import { persistedAgentRunSchema } from "./api/schemas";

export const AGENT_RUN_STORAGE_KEY = "enterprise-doc.agent-run.v1";

export class AgentPersistenceError extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "AgentPersistenceError";
  }
}

export interface AgentRunRecoveryStore {
  load(): PersistedAgentRun | null;
  save(value: PersistedAgentRun): void;
  clear(): void;
}

export function createAgentRunRecoveryStore(
  storage: Storage,
  key = AGENT_RUN_STORAGE_KEY,
): AgentRunRecoveryStore {
  return {
    load() {
      let raw: string | null;
      try {
        raw = storage.getItem(key);
      } catch (error) {
        throw new AgentPersistenceError("Agent run recovery storage could not be read.", {
          cause: error,
        });
      }
      if (raw === null) {
        return null;
      }
      try {
        const parsed = persistedAgentRunSchema.safeParse(JSON.parse(raw) as unknown);
        if (parsed.success) {
          return parsed.data;
        }
      } catch {
        // Invalid JSON is handled like any other invalid recovery record.
      }
      try {
        storage.removeItem(key);
      } catch (error) {
        throw new AgentPersistenceError("Invalid Agent run recovery storage could not be cleared.", {
          cause: error,
        });
      }
      return null;
    },

    save(value) {
      const parsed = persistedAgentRunSchema.safeParse(value);
      if (!parsed.success) {
        throw new AgentPersistenceError("Agent run recovery record does not match its runtime schema.", {
          cause: parsed.error,
        });
      }
      try {
        storage.setItem(key, JSON.stringify(parsed.data));
      } catch (error) {
        throw new AgentPersistenceError("Agent run recovery storage could not be written.", {
          cause: error,
        });
      }
    },

    clear() {
      try {
        storage.removeItem(key);
      } catch (error) {
        throw new AgentPersistenceError("Agent run recovery storage could not be cleared.", {
          cause: error,
        });
      }
    },
  };
}
