import { expect, type Page } from "@playwright/test";

export function observeNetwork(pages: Page[]) {
  const application: { method: string; path: string; status: number; context: boolean; csrf: boolean; cookie: boolean; authorization: boolean; noStore: boolean }[] = [];
  const objects: { status: number; cookie: boolean; authorization: boolean; context: boolean; csrf: boolean }[] = [];
  const workers: string[] = [];
  const pageErrors: string[] = [];
  const pending: Promise<void>[] = [];
  for (const page of pages) {
    page.on("worker", worker => workers.push(new URL(worker.url()).pathname));
    page.on("pageerror", error => pageErrors.push(error.name));
    page.on("response", response => {
      const url = new URL(response.url());
      const request = response.request();
      if (url.pathname.startsWith("/api/") || url.pathname === "/auth/admission/accept" || url.pathname.startsWith("/auth/invitations/")) {
        pending.push((async () => {
          const headers = await request.allHeaders();
          const reply = await response.allHeaders();
          application.push({ method: request.method(), path: url.pathname, status: response.status(),
            context: Boolean(headers["x-session-context"]), csrf: Boolean(headers["x-csrf-token"]),
            cookie: Boolean(headers.cookie?.includes("__Host-docagent-session=")), authorization: Boolean(headers.authorization),
            noStore: Boolean(reply["cache-control"]?.includes("no-store")),
          });
        })());
      }
      if (url.port === "9000" && request.method() === "PUT") {
        pending.push((async () => {
          const headers = await request.allHeaders();
          objects.push({ status: response.status(), cookie: Boolean(headers.cookie), authorization: Boolean(headers.authorization), context: Boolean(headers["x-session-context"]), csrf: Boolean(headers["x-csrf-token"]) });
        })());
      }
    });
  }
  return async (expectedUploads: number) => {
    await Promise.all(pending);
    const successful = application.filter(item => item.status < 400);
    expect(successful.length).toBeGreaterThan(10);
    expect(successful.every(item => item.context && item.cookie && !item.authorization && item.noStore && (item.method === "GET" || item.csrf))).toBe(true);
    expect(objects).toHaveLength(expectedUploads);
    expect(objects.every(item => item.status === 200 && !item.cookie && !item.authorization && !item.context && !item.csrf)).toBe(true);
    // Each upload also waits for its own real hash Worker. Multipart negotiation
    // can start an additional hash job, so Worker count is not file count.
    expect(workers.filter(name => name.includes("hash.worker")).length).toBeGreaterThanOrEqual(expectedUploads);
    expect(pageErrors).toEqual([]);
    return { application, objects, workers, pageErrors };
  };
}
