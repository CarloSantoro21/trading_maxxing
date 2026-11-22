/**
 * Pages Function: reenvía todo `/api/*` al Worker de la API por service binding.
 *
 * Cloudflare Access protege el dominio de Pages: el navegador solo llega aquí con sesión y
 * Access agrega el JWT en `Cf-Access-Jwt-Assertion` (y la cookie `CF_Authorization`). La
 * petición se reenvía completa (método, URL, headers y body) a `env.API`, el service
 * binding al Worker `tm-api-dev` / `tm-api`, que valida ese JWT. Así el frontend y la API
 * comparten origen: sin CORS y sin exponer la URL del Worker al navegador.
 *
 * Solo `/api/*` invoca la función (Pages genera `_routes.json` a partir de esta carpeta);
 * el resto del sitio son los archivos estáticos de `out/`. El binding `API` se configura en
 * el proyecto de Pages (Terraform de trading_maxxing-infra).
 */

export interface Env {
  /** Service binding al Worker de la API. */
  API?: Fetcher;
}

function jsonError(status: number, detail: string): Response {
  return new Response(JSON.stringify({ detail }), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
  });
}

/** Reenvía la petición tal cual al Worker y devuelve su respuesta sin tocarla. */
export async function proxyToApi(request: Request, env: Env): Promise<Response> {
  if (!env.API) {
    return jsonError(503, "Falta el service binding API en el proyecto de Pages; no se puede llegar a la API.");
  }
  try {
    return await env.API.fetch(new Request(request));
  } catch {
    return jsonError(502, "La API no respondió. Intenta de nuevo en unos segundos.");
  }
}

export const onRequest: PagesFunction<Env> = ({ request, env }) => proxyToApi(request, env);
