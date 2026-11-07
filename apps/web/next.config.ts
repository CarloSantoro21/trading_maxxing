import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Sitio estático: `pnpm build` genera out/, que se publica en Cloudflare Pages (#45)
  // y que la imagen Docker sirve con nginx. No hay render en servidor, route handlers
  // ni middleware; `pnpm check:export` verifica las rutas generadas.
  output: "export",
  // El optimizador de imágenes necesita un servidor; en un export estático va apagado.
  images: { unoptimized: true },
};

export default nextConfig;
