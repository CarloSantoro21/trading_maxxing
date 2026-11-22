import type { NextConfig } from "next";

/**
 * Producción (`pnpm build`): export estático en out/, que se publica en Cloudflare Pages
 * junto con `functions/` (la Pages Function que reenvía /api/* al Worker). No hay render en
 * servidor, route handlers ni middleware; `pnpm check:export` verifica las rutas generadas.
 *
 * Desarrollo (`pnpm dev`): un export no admite rewrites, así que solo en dev se quita
 * `output: "export"` y `next dev` reenvía /api/* a `wrangler dev` (apps/api, puerto 8787,
 * con AUTH_MODE=dev). Así el navegador siempre llama al mismo origen, igual que en Pages.
 * `API_DEV_URL` cambia el destino (p. ej. un mock de los E2E).
 */
const isDev = process.env.NODE_ENV === "development";
const apiDevUrl = (process.env.API_DEV_URL ?? "http://localhost:8787").replace(/\/+$/, "");

const nextConfig: NextConfig = {
  // El optimizador de imágenes necesita un servidor; en un export estático va apagado.
  images: { unoptimized: true },
  ...(isDev
    ? {
        async rewrites() {
          return [{ source: "/api/:path*", destination: `${apiDevUrl}/api/:path*` }];
        },
      }
    : { output: "export" as const }),
};

export default nextConfig;
