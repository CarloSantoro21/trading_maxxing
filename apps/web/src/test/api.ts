import { vi } from "vitest";

/**
 * `fetch` simulado para las llamadas a services/api (sin red). Cada respuesta se elige con
 * `handler(método, ruta, cuerpo)`; las llamadas quedan en `calls`.
 */

export interface ApiCall {
  method: string;
  path: string;
  body: unknown;
  headers: Record<string, string>;
}

export type ApiHandler = (call: ApiCall) => { status?: number; body?: unknown } | Promise<{ status?: number; body?: unknown }>;

export const API_URL = "http://api.test";

export function mockApi(handler: ApiHandler = () => ({ status: 200, body: {} })) {
  vi.stubEnv("NEXT_PUBLIC_API_URL", API_URL);
  const calls: ApiCall[] = [];
  const fetchMock = vi.fn(async (url: string, init: RequestInit = {}) => {
    const call: ApiCall = {
      method: init.method ?? "GET",
      path: url.replace(API_URL, ""),
      body: init.body ? JSON.parse(String(init.body)) : undefined,
      headers: (init.headers ?? {}) as Record<string, string>,
    };
    calls.push(call);
    const { status = 200, body } = await handler(call);
    return new Response(body === undefined ? "" : JSON.stringify(body), { status });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}
