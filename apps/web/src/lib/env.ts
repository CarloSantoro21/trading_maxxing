/**
 * Ambiente y versión del build, para el badge del encabezado (sirve para enseñar en la
 * demo qué versión está desplegada en cada ambiente). Next.js incrusta las variables
 * `NEXT_PUBLIC_*` al construir: cada deploy lleva las suyas.
 */

export type AppEnv = "local" | "dev" | "prod";

export function appEnv(): AppEnv {
  // Next.js reemplaza la referencia en tiempo de build; debe escribirse literal.
  const value = (process.env.NEXT_PUBLIC_ENV ?? "").trim().toLowerCase();
  if (value === "dev" || value === "prod") return value;
  if (value === "production") return "prod";
  return "local";
}

/** SHA corto (7 caracteres) del commit construido, o null si no se definió. */
export function commitSha(): string | null {
  const sha = (process.env.NEXT_PUBLIC_COMMIT_SHA ?? "").trim();
  return sha ? sha.slice(0, 7) : null;
}
