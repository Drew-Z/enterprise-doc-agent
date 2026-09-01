import { useEffect, useState } from "react";

export type ProductRoute = "overview" | "documents" | "agent-runs" | "audit" | "identity" | "runtime";

const routePaths: Record<ProductRoute, string> = {
  overview: "/overview",
  documents: "/documents",
  "agent-runs": "/agent-runs",
  audit: "/audit",
  identity: "/identity",
  runtime: "/runtime",
};

function routeFromHash(hash: string): ProductRoute {
  const path = hash.replace(/^#/, "").replace(/\/$/, "") || "/overview";
  const match = Object.entries(routePaths).find(([, value]) => value === path);
  return match?.[0] as ProductRoute | undefined ?? "overview";
}

export function useProductRoute(): [ProductRoute, (route: ProductRoute) => void] {
  const [route, setRoute] = useState<ProductRoute>(() => routeFromHash(window.location.hash));

  useEffect(() => {
    const handleHashChange = () => setRoute(routeFromHash(window.location.hash));
    window.addEventListener("hashchange", handleHashChange);
    window.addEventListener("popstate", handleHashChange);
    return () => {
      window.removeEventListener("hashchange", handleHashChange);
      window.removeEventListener("popstate", handleHashChange);
    };
  }, []);

  const navigate = (nextRoute: ProductRoute) => {
    const nextHash = `#${routePaths[nextRoute]}`;
    if (window.location.hash !== nextHash) {
      window.history.pushState(null, "", nextHash);
    }
    setRoute(nextRoute);
    document.documentElement.scrollTop = 0;
    document.body.scrollTop = 0;
  };

  return [route, navigate];
}
