import type { Session } from "@supabase/supabase-js";
import { vi } from "vitest";
import { createFakeSupabase } from "./fakeSupabase";

/** Fetch falso que responde según "MÉTODO /ruta" (sin red). */
export type Route = (init: RequestInit | undefined) => { status?: number; body: unknown };

export function fakeFetcher(routes: Record<string, Route | unknown>) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input));
    const key = `${init?.method ?? "GET"} ${url.pathname}`;
    if (!(key in routes)) return new Response(JSON.stringify({ detail: `sin ruta ${key}` }), { status: 404 });
    const route = routes[key];
    const isReply = (r: unknown): r is { status: number; body: unknown } =>
      Boolean(r && typeof r === "object" && "status" in r && "body" in r);
    const { status = 200, body } =
      typeof route === "function" ? (route as Route)(init) : isReply(route) ? route : { status: 200, body: route };
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  }) as unknown as typeof fetch & ReturnType<typeof vi.fn>;
}

/** Cliente de Supabase falso con una sesión que trae access_token. */
export function sessionClient() {
  const fake = createFakeSupabase(() => ({ data: null }));
  fake.auth.getSession.mockResolvedValue({
    data: { session: { user: { id: "user-1" }, access_token: "tok" } as unknown as Session },
    error: null,
  });
  return fake.client;
}

// Recharts usa ResizeObserver, que jsdom no trae.
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}
