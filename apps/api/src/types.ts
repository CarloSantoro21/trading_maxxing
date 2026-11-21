/** Tipos compartidos del Worker: bindings, variables del contexto y usuario actual. */

export type Role = "admin" | "member";

export interface Bindings {
  DB: D1Database;
  /** "dev" (usuario fijo, solo local y pruebas) o "access" (JWT de Cloudflare Access). */
  AUTH_MODE?: string;
  /** "dev" o "production". En production nunca se acepta AUTH_MODE=dev. */
  ENVIRONMENT?: string;
  /** Team domain de Zero Trust, p. ej. "mi-equipo.cloudflareaccess.com". */
  ACCESS_TEAM_DOMAIN?: string;
  /** Application Audience (AUD) Tag de la aplicación de Access. */
  ACCESS_AUD?: string;
  /** Client IDs de service tokens de Access aceptados en /internal/* (separados por coma). */
  ACCESS_SERVICE_CLIENT_IDS?: string;
  /** Correo del usuario fijo con AUTH_MODE=dev. */
  DEV_USER_EMAIL?: string;
  /** Secreto que el motor manda en X-Service-Token (wrangler secret put SERVICE_TOKEN). */
  SERVICE_TOKEN?: string;
  /** Orígenes permitidos por CORS, separados por coma. */
  CORS_ORIGINS?: string;
  /** Segundos sin latido para considerar el motor fuera de línea (default 120). */
  ENGINE_STALE_SECONDS?: string;
}

export interface CurrentUser {
  id: string;
  email: string;
  role: Role;
  created_at: string;
}

export interface Variables {
  requestId: string;
  user: CurrentUser;
  /** Quién llamó a /internal/*: "service-token" o "access:<client_id>". */
  service: string;
}

export interface AppEnv {
  Bindings: Bindings;
  Variables: Variables;
}
