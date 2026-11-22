/**
 * Cliente de la API (Cloudflare Worker `apps/api`) para el navegador.
 *
 * Todo pasa por el mismo origen: en Cloudflare Pages la Pages Function
 * `functions/api/[[path]].ts` reenvía `/api/*` al Worker por service binding, y en local
 * `next dev` lo reenvía a `wrangler dev` (http://localhost:8787, ver next.config.ts). Así no
 * hay CORS y la cookie de Cloudflare Access (`CF_Authorization`) viaja sola: el frontend no
 * maneja tokens. El contrato está en docs/arquitectura/api.md.
 *
 * `NEXT_PUBLIC_API_BASE` (opcional) apunta las llamadas a otro origen, p. ej.
 * `http://localhost:8787` para hablarle directo al Worker local sin el proxy de `next dev`.
 */

export const API_PREFIX = "/api";

export function apiBase(): string {
  // Next.js reemplaza la referencia en tiempo de build; debe escribirse literal.
  const origin = (process.env.NEXT_PUBLIC_API_BASE ?? "").trim().replace(/\/+$/, "");
  return `${origin}${API_PREFIX}`;
}

export class ApiError extends Error {
  readonly status: number;
  /** Cuerpo de la respuesta de error (p. ej. `{detail, request_id}` en un 500). */
  readonly body: unknown;

  constructor(status: number, message: string, body?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

/** Mensaje legible del cuerpo de error de la API (`detail` texto o lista de validación). */
export function errorDetail(body: unknown, status: number): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      const msgs = detail
        .map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : ""))
        .filter(Boolean);
      if (msgs.length) return msgs.join("; ");
    }
  }
  if (status === 401) return "Tu sesión de Cloudflare Access expiró; recarga la página para volver a entrar.";
  if (status === 403) return "No tienes permiso para hacer esto.";
  return `La API respondió con un error (${status}).`;
}

export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export interface ApiRequestOptions {
  method?: HttpMethod;
  body?: unknown;
  signal?: AbortSignal;
}

/**
 * Llama a `/api{path}` y devuelve el JSON (o `undefined` con 204). Lanza `ApiError` con el
 * `detail` de la API si la respuesta no es 2xx, y con status 0 si no hubo conexión.
 */
export async function apiFetch<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  const { method = "GET", body, signal } = options;
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";

  let response: Response;
  try {
    response = await fetch(`${apiBase()}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "include",
      // Si la sesión de Access expiró, Access responde con una redirección a su login;
      // no la seguimos (fallaría por CORS) y la tratamos como 401.
      redirect: "manual",
      signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(0, "No se pudo conectar con la API. Revisa tu conexión y vuelve a intentar.");
  }

  if (response.type === "opaqueredirect") throw new ApiError(401, errorDetail(null, 401));

  const text = await response.text();
  let parsed: unknown = undefined;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = text;
    }
  }
  if (!response.ok) throw new ApiError(response.status, errorDetail(parsed, response.status), parsed);
  return parsed as T;
}

/** Fetcher para SWR: la llave es la ruta (sin `/api`). */
export function swrFetcher<T>(path: string): Promise<T> {
  return apiFetch<T>(path);
}

/** Mensaje para mostrar al usuario a partir de cualquier error de una llamada. */
export function apiErrorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return "Ocurrió un error inesperado. Intenta de nuevo.";
}

// --- usuario y sesión ------------------------------------------------------------------

export type Role = "admin" | "member";

export interface Profile {
  id: string;
  user_id: string;
  horizon: string | null;
  risk_level: number;
  capital: number;
  max_drawdown: number | null;
  instruments: string[] | null;
  created_at: string;
  updated_at: string;
}

/** GET /api/me */
export interface Me {
  id: string;
  email: string;
  role: Role;
  profile: Profile | null;
}

// --- bots ------------------------------------------------------------------------------

export type BotStatus = "stopped" | "running" | "paused" | "killed";

export interface ApiBot {
  id: string;
  profile_id: string;
  name: string;
  status: BotStatus;
  started_at: string | null;
  stopped_at: string | null;
}

export interface BotCommandResult {
  bot: ApiBot;
  command: "start" | "stop" | "kill";
  command_id: string;
}

export function createBot(name: string): Promise<ApiBot> {
  return apiFetch<ApiBot>("/bots", { method: "POST", body: { name } });
}

export function startBot(botId: string): Promise<BotCommandResult> {
  return apiFetch(`/bots/${encodeURIComponent(botId)}/start`, { method: "POST" });
}

export function stopBot(botId: string): Promise<BotCommandResult> {
  return apiFetch(`/bots/${encodeURIComponent(botId)}/stop`, { method: "POST" });
}

export function killBot(botId: string, reason: string): Promise<BotCommandResult> {
  return apiFetch(`/bots/${encodeURIComponent(botId)}/kill`, { method: "POST", body: { reason } });
}

// --- estrategias -----------------------------------------------------------------------

export type StrategyAction = "promote" | "retire";

export interface StrategyTransitionResult {
  from_state: string;
  to_state: string;
  actor: string;
  reason: string;
}

export function transitionStrategy(name: string, action: StrategyAction, reason: string): Promise<StrategyTransitionResult> {
  return apiFetch(`/strategies/${encodeURIComponent(name)}/${action}`, { method: "POST", body: { reason } });
}

// --- motor -----------------------------------------------------------------------------

export interface EngineComponent {
  component: string;
  status: string;
  version: string | null;
  host: string | null;
  started_at: string | null;
  last_seen_at: string;
  age_seconds: number;
  online: boolean;
  details?: unknown;
}

/** GET /api/engine/status */
export interface EngineStatus {
  online: boolean;
  last_heartbeat_at: string | null;
  stale_after_seconds: number;
  components: EngineComponent[];
}

// --- ajustes y administración ----------------------------------------------------------

export interface Settings {
  news_enabled: boolean;
  explainer_enabled: boolean;
}

export interface AdminUser {
  id: string;
  email: string;
  role: Role;
  created_at: string;
  updated_at: string;
}

export function createUser(email: string, role: Role): Promise<AdminUser> {
  return apiFetch("/admin/users", { method: "POST", body: { email, role } });
}

export function updateUserRole(id: string, role: Role): Promise<AdminUser> {
  return apiFetch(`/admin/users/${encodeURIComponent(id)}`, { method: "PATCH", body: { role } });
}

export function deleteUser(id: string): Promise<void> {
  return apiFetch(`/admin/users/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export function updateSettings(patch: Partial<Settings>): Promise<Settings> {
  return apiFetch("/admin/settings", { method: "PUT", body: patch });
}

/** Error 500 a propósito para la demo de APM; devuelve el request_id que dio la API. */
export async function triggerDemoError(): Promise<string | null> {
  try {
    await apiFetch("/admin/debug/error", { method: "POST" });
    return null;
  } catch (err) {
    if (err instanceof ApiError && err.status === 500) {
      const body = err.body as { request_id?: unknown } | undefined;
      return typeof body?.request_id === "string" ? body.request_id : null;
    }
    throw err;
  }
}
