/**
 * Thin fetch wrapper for the admin API.
 *
 * The bearer token comes from VITE_ADMIN_API_TOKEN at build time, or you can
 * inject it at runtime by setting window.__VALGO_TOKEN before mount.
 */
const TOKEN = import.meta.env.VITE_ADMIN_API_TOKEN || (window as any).__VALGO_TOKEN || "";
const BASE = "/api";

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${TOKEN}`,
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${method} ${path} → ${res.status}`);
  return res.json();
}

export const api = {
  strategies: {
    list: () => request<{ strategies: any[] }>("GET", "/strategies"),
    save: (strategies: any[]) => request("PUT", "/strategies", { strategies }),
  },
  dataSources: {
    list: () => request<{ sources: any[] }>("GET", "/data-sources"),
    save: (sources: any[]) => request("PUT", "/data-sources", { sources }),
  },
  accounts: {
    list: () => request<{ accounts: any[] }>("GET", "/accounts"),
    save: (accounts: any[]) => request("PUT", "/accounts", { accounts }),
  },
  nodes: {
    list: () => request<{ nodes: any[] }>("GET", "/nodes"),
    save: (nodes: any[]) => request("PUT", "/nodes", { nodes }),
  },
  signals: {
    list: () => request<{ signals: any[] }>("GET", "/signals"),
    save: (signals: any[]) => request("PUT", "/signals", { signals }),
  },
  risk: {
    get: () => request<any>("GET", "/risk"),
    save: (limits: any) => request("PUT", "/risk", limits),
  },
  audit: {
    list: (limit = 100) => request<{ events: any[]; count: number }>("GET", `/audit?limit=${limit}`),
  },
};
