import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { BrowserSessionBoundary } from "./auth/BrowserSessionBoundary";
import { consumeAuthEntry } from "./auth/entry";
import { configureAuthentication } from "./auth/transport";
import { isShowcaseMode } from "./product/showcase";

const browserMode = import.meta.env.VITE_AUTH_MODE !== "bearer" && !isShowcaseMode();
configureAuthentication(browserMode ? "browser" : "bearer");
const authEntry = browserMode ? consumeAuthEntry() : undefined;

const root = document.getElementById("root");
const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: false, refetchOnWindowFocus: false },
  },
});

if (!root) {
  throw new Error("Root element is missing");
}

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      {browserMode ? <BrowserSessionBoundary entry={authEntry}>{session => <App browserSession={session} />}</BrowserSessionBoundary> : <App />}
    </QueryClientProvider>
  </StrictMode>,
);
