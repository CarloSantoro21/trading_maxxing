/**
 * Middlewares de autenticación.
 *
 * `requireUser` (rutas /api/*):
 *   * AUTH_MODE=access: verifica el JWT de Cloudflare Access y busca el correo en la
 *     tabla users. Si el correo no está dado de alta: 403 (un admin lo da de alta).
 *   * AUTH_MODE=dev: usa el header X-Dev-User o DEV_USER_EMAIL. Solo para wrangler dev
 *     --local y las pruebas; con ENVIRONMENT=production se rechaza (500 de configuración).
 *
 * `requireService` (rutas /internal/*): el motor de la Mac manda X-Service-Token igual al
 * secreto SERVICE_TOKEN. También se acepta un JWT de Access emitido para un service token
 * (claim common_name), si su Client ID está en ACCESS_SERVICE_CLIENT_IDS.
 *
 * `requireAdmin`: 403 si el usuario no es admin.
 */

import type { MiddlewareHandler } from "hono";
import { ApiError } from "../lib/errors";
import type { AppEnv, Bindings, CurrentUser } from "../types";
import { AccessError, accessToken, verifyAccessJwt } from "./access";

const unauthorized = (detail: string): ApiError => new ApiError(401, detail);

function authMode(env: Bindings): "dev" | "access" {
  const mode = (env.AUTH_MODE ?? "access").toLowerCase();
  if (mode !== "dev" && mode !== "access") {
    throw new ApiError(500, `AUTH_MODE inválido: ${mode} (usa dev o access)`);
  }
  if (mode === "dev" && env.ENVIRONMENT === "production") {
    throw new ApiError(500, "AUTH_MODE=dev no está permitido en production");
  }
  return mode;
}

export async function findUserByEmail(db: D1Database, email: string): Promise<CurrentUser | null> {
  return db
    .prepare("SELECT id, email, role, created_at FROM users WHERE email = ?")
    .bind(email.trim().toLowerCase())
    .first<CurrentUser>();
}

async function emailFromRequest(env: Bindings, headers: Headers): Promise<string> {
  if (authMode(env) === "dev") {
    const email = headers.get("X-Dev-User") ?? env.DEV_USER_EMAIL;
    if (!email) throw unauthorized("AUTH_MODE=dev sin DEV_USER_EMAIL ni header X-Dev-User");
    return email;
  }
  const token = accessToken(headers);
  if (!token) throw unauthorized("Falta el header Cf-Access-Jwt-Assertion (entra por Cloudflare Access)");
  try {
    const claims = await verifyAccessJwt(token, { teamDomain: env.ACCESS_TEAM_DOMAIN, aud: env.ACCESS_AUD });
    if (typeof claims.email !== "string" || !claims.email) {
      throw unauthorized("El token de Access no trae correo (¿es un service token?)");
    }
    return claims.email;
  } catch (err) {
    if (err instanceof AccessError) throw unauthorized(`No autorizado: ${err.message}`);
    throw err;
  }
}

export const requireUser: MiddlewareHandler<AppEnv> = async (c, next) => {
  const email = await emailFromRequest(c.env, c.req.raw.headers);
  const user = await findUserByEmail(c.env.DB, email);
  if (!user) {
    throw new ApiError(403, `El correo ${email.toLowerCase()} no está dado de alta; pídele a un admin que te agregue`);
  }
  c.set("user", user);
  await next();
};

export const requireAdmin: MiddlewareHandler<AppEnv> = async (c, next) => {
  if (c.get("user").role !== "admin") {
    throw new ApiError(403, "Solo un admin puede hacer esta acción");
  }
  await next();
};

/** Comparación en tiempo constante (evita filtrar el token por tiempos de respuesta). */
export function safeEqual(a: string, b: string): boolean {
  const enc = new TextEncoder();
  const x = enc.encode(a);
  const y = enc.encode(b);
  let diff = x.length ^ y.length;
  const n = Math.max(x.length, y.length);
  for (let i = 0; i < n; i++) diff |= (x[i] ?? 0) ^ (y[i] ?? 0);
  return diff === 0;
}

const csv = (raw: string | undefined): string[] =>
  (raw ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

export const requireService: MiddlewareHandler<AppEnv> = async (c, next) => {
  const sent = c.req.header("X-Service-Token");
  const secret = c.env.SERVICE_TOKEN;
  if (sent) {
    if (!secret) throw new ApiError(503, "SERVICE_TOKEN no está configurado en el Worker");
    if (!safeEqual(sent, secret)) throw unauthorized("X-Service-Token inválido");
    c.set("service", "service-token");
    return next();
  }
  const allowed = csv(c.env.ACCESS_SERVICE_CLIENT_IDS);
  const token = accessToken(c.req.raw.headers);
  if (token && allowed.length > 0) {
    try {
      const claims = await verifyAccessJwt(token, { teamDomain: c.env.ACCESS_TEAM_DOMAIN, aud: c.env.ACCESS_AUD });
      const clientId = typeof claims.common_name === "string" ? claims.common_name : "";
      if (clientId && allowed.includes(clientId)) {
        c.set("service", `access:${clientId}`);
        return next();
      }
      throw new ApiError(403, "El token de Access no es de un service token autorizado");
    } catch (err) {
      if (err instanceof AccessError) throw unauthorized(`No autorizado: ${err.message}`);
      throw err;
    }
  }
  throw unauthorized("Falta X-Service-Token");
};
