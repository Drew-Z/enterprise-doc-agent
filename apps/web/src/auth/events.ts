export const SESSION_CHANGE_KEY = "enterprise-doc.browser-session.changed.v1";
const CHANNEL_NAME = "enterprise-doc.browser-session.v1";

export function createSessionEvents(onChange: () => void) {
  let channel: BroadcastChannel | null = null;
  try { channel = new BroadcastChannel(CHANNEL_NAME); } catch { /* Storage and visibility remain available. */ }
  if (channel) channel.onmessage = event => { if (event.data === "invalidate") onChange(); };
  const storageChanged = (event: StorageEvent) => { if (event.key === SESSION_CHANGE_KEY) onChange(); };
  window.addEventListener("storage", storageChanged);
  return {
    publish() {
      channel?.postMessage("invalidate");
      // The fallback contains only a random change marker, never identity or credentials.
      if (!channel) { try { localStorage.setItem(SESSION_CHANGE_KEY, crypto.randomUUID()); } catch { /* Reconcile on visibility. */ } }
    },
    close() { channel?.close(); window.removeEventListener("storage", storageChanged); },
  };
}
