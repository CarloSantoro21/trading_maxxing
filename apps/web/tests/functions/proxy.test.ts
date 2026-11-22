// @vitest-environment node
// Vive fuera de functions/ para que Pages no lo trate como ruta; lo compila functions/tsconfig.json.
import { describe, expect, it, vi } from "vitest";
import { onRequest, proxyToApi, type Env } from "../../functions/api/[[path]]";

function binding(handler: (req: Request) => Response | Promise<Response>) {
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => handler(new Request(input, init)));
  return { env: { API: { fetch } as unknown as Fetcher } satisfies Env, fetch };
}

describe("Pages Function /api/*", () => {
  it("reenvía método, URL, headers (JWT de Access y cookie) y body al Worker", async () => {
    let seen: { method: string; url: string; jwt: string | null; cookie: string | null; body: string } | null = null;
    const { env } = binding(async (req) => {
      seen = {
        method: req.method,
        url: req.url,
        jwt: req.headers.get("Cf-Access-Jwt-Assertion"),
        cookie: req.headers.get("Cookie"),
        body: await req.text(),
      };
      return new Response(JSON.stringify({ ok: true }), { status: 201, headers: { "X-Request-Id": "r1" } });
    });
    const request = new Request("https://tm-web-dev.pages.dev/api/onboarding?x=1", {
      method: "PUT",
      headers: { "Cf-Access-Jwt-Assertion": "jwt.de.access", Cookie: "CF_Authorization=abc", "Content-Type": "application/json" },
      body: JSON.stringify({ risk_level: 3 }),
    });
    const res = await proxyToApi(request, env);
    expect(res.status).toBe(201);
    expect(res.headers.get("X-Request-Id")).toBe("r1");
    expect(await res.json()).toEqual({ ok: true });
    expect(seen).toEqual({
      method: "PUT",
      url: "https://tm-web-dev.pages.dev/api/onboarding?x=1",
      jwt: "jwt.de.access",
      cookie: "CF_Authorization=abc",
      body: '{"risk_level":3}',
    });
  });

  it("devuelve los errores del Worker sin cambiarlos", async () => {
    const { env } = binding(() => new Response(JSON.stringify({ detail: "No autorizado" }), { status: 403 }));
    const res = await proxyToApi(new Request("https://x.pages.dev/api/me"), env);
    expect(res.status).toBe(403);
    expect(await res.json()).toEqual({ detail: "No autorizado" });
  });

  it("responde 503 si falta el service binding", async () => {
    const res = await proxyToApi(new Request("https://x.pages.dev/api/me"), {});
    expect(res.status).toBe(503);
    expect(((await res.json()) as { detail: string }).detail).toMatch(/service binding API/);
  });

  it("responde 502 si el Worker no contesta", async () => {
    const { env } = binding(() => {
      throw new Error("caído");
    });
    const res = await proxyToApi(new Request("https://x.pages.dev/api/me"), env);
    expect(res.status).toBe(502);
  });

  it("onRequest usa el request y el env del contexto", async () => {
    const { env, fetch } = binding(() => new Response("{}", { status: 200 }));
    const context = { request: new Request("https://x.pages.dev/api/health"), env } as unknown as Parameters<typeof onRequest>[0];
    const res = await onRequest(context);
    expect(res.status).toBe(200);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
