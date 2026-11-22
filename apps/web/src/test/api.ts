import { vi } from "vitest";
import type { EngineStatus, Me } from "@/lib/api/client";

/**
 * `fetch` simulado para la API (`/api/*`), sin red. Cada ruta se define como
 * `"MÉTODO /ruta"` (sin el prefijo `/api` ni la query) y responde con un cuerpo fijo,
 * `{status, body}` o una función de la llamada. Las llamadas quedan en `calls`.
 *
 * `GET /me` y `GET /engine/status` tienen respuestas por defecto (usuario demo y motor en
 * línea) porque las piden `RequireAuth` y el encabezado en casi todas las pruebas.
 */

export interface ApiCall {
  method: string;
  /** Ruta sin `/api` ni query, p. ej. `/bots/b1/start`. */
  path: string;
  /** Query string, p. ej. `?limit=5` (vacío si no hay). */
  search: string;
  body: unknown;
  headers: Record<string, string>;
}

export interface Reply {
  status?: number;
  body?: unknown;
}

export type RouteHandler = (call: ApiCall) => Reply | Promise<Reply>;
export type Routes = Record<string, RouteHandler | Reply | unknown>;

export const DEMO_ME: Me = {
  id: "user-1",
  email: "demo@trading-maxxing.local",
  role: "member",
  profile: null,
};

export const ENGINE_ONLINE: EngineStatus = {
  online: true,
  last_heartbeat_at: "2026-11-21T10:00:00.000Z",
  stale_after_seconds: 120,
  components: [],
};

function isReply(value: unknown): value is Reply {
  return Boolean(value && typeof value === "object" && ("status" in value || "body" in value) && Object.keys(value).every((k) => k === "status" || k === "body"));
}

export function mockApi(routes: Routes = {}) {
  const all: Routes = { "GET /me": DEMO_ME, "GET /engine/status": ENGINE_ONLINE, ...routes };
  const calls: ApiCall[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(String(input), "http://localhost");
    const call: ApiCall = {
      method: init.method ?? "GET",
      path: url.pathname.replace(/^\/api/, ""),
      search: url.search,
      body: init.body ? JSON.parse(String(init.body)) : undefined,
      headers: (init.headers ?? {}) as Record<string, string>,
    };
    calls.push(call);
    const key = `${call.method} ${call.path}`;
    if (!(key in all)) {
      return new Response(JSON.stringify({ detail: `sin ruta ${key}` }), { status: 404 });
    }
    const route = all[key];
    const { status = 200, body } =
      typeof route === "function" ? await (route as RouteHandler)(call) : isReply(route) ? route : { body: route };
    const payload = body === undefined || status === 204 ? null : JSON.stringify(body);
    return new Response(payload, { status, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  /** Llamadas que no son las de fondo (sesión y motor). */
  const actions = () => calls.filter((c) => !(c.method === "GET" && (c.path === "/me" || c.path === "/engine/status")));
  return { calls, actions, fetchMock };
}
