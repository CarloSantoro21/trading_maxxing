/**
 * Cliente de services/api (FastAPI) para el navegador.
 *
 * Las lecturas van directo a Supabase con supabase-js (RLS), pero las acciones con efectos
 * fuera de la base pasan por la API: arrancar, detener y el paro de emergencia publican en
 * el stream `tm:commands` de Redis (#21), y promover o retirar estrategias exige el rol de
 * admin (#67). Cada llamada lleva el `access_token` de la sesión de Supabase en
 * `Authorization: Bearer`, así que la API (y RLS detrás de ella) sabe quién es.
 */

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export class MissingApiConfigError extends Error {
  constructor() {
    super("Falta NEXT_PUBLIC_API_URL (p. ej. http://127.0.0.1:8000) en apps/web/.env.local.");
    this.name = "MissingApiConfigError";
  }
}

export function getApiUrl(): string | null {
  // Next.js reemplaza esta referencia en tiempo de build; debe escribirse literal.
  const url = process.env.NEXT_PUBLIC_API_URL;
  return url ? url.replace(/\/+$/, "") : null;
}

/** Mensaje legible a partir del cuerpo de error de FastAPI (`detail` texto o lista). */
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
  if (status === 401) return "Tu sesión expiró; vuelve a iniciar sesión.";
  if (status === 403) return "No tienes permiso para hacer esto.";
  return `La API respondió con un error (${status}).`;
}

export interface ApiRequestOptions {
  token: string;
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  /** Inyectable para pruebas. */
  fetchImpl?: typeof fetch;
}

export async function apiRequest<T>(path: string, options: ApiRequestOptions): Promise<T> {
  const base = getApiUrl();
  if (!base) throw new MissingApiConfigError();
  const { token, method = "GET", body, fetchImpl = fetch } = options;
  const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
  if (body !== undefined) headers["Content-Type"] = "application/json";

  let response: Response;
  try {
    response = await fetchImpl(`${base}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, `No se pudo conectar con la API en ${base}. ¿Está corriendo \`make up\`?`);
  }

  const text = await response.text();
  let parsed: unknown = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = text;
    }
  }
  if (!response.ok) throw new ApiError(response.status, errorDetail(parsed, response.status));
  return parsed as T;
}

// --- bots (#21) ------------------------------------------------------------------------

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

type Opts = Omit<ApiRequestOptions, "method" | "body">;

export function createBot(name: string, opts: Opts): Promise<ApiBot> {
  return apiRequest<ApiBot>("/bots", { ...opts, method: "POST", body: { name } });
}

export function startBot(botId: string, opts: Opts): Promise<BotCommandResult> {
  return apiRequest(`/bots/${encodeURIComponent(botId)}/start`, { ...opts, method: "POST" });
}

export function stopBot(botId: string, opts: Opts): Promise<BotCommandResult> {
  return apiRequest(`/bots/${encodeURIComponent(botId)}/stop`, { ...opts, method: "POST" });
}

export function killBot(botId: string, reason: string, opts: Opts): Promise<BotCommandResult> {
  return apiRequest(`/bots/${encodeURIComponent(botId)}/kill`, {
    ...opts,
    method: "POST",
    body: { reason },
  });
}

// --- estrategias (#58, #67) -------------------------------------------------------------

export type StrategyAction = "promote" | "retire";

export interface StrategyTransitionResult {
  from_state: string;
  to_state: string;
  actor: string;
  reason: string;
}

export function transitionStrategy(
  name: string,
  action: StrategyAction,
  reason: string,
  opts: Opts,
): Promise<StrategyTransitionResult> {
  return apiRequest(`/strategies/${encodeURIComponent(name)}/${action}`, {
    ...opts,
    method: "POST",
    body: { reason },
  });
}

/** Mensaje para mostrar al usuario a partir de cualquier error de una llamada. */
export function apiErrorMessage(err: unknown): string {
  if (err instanceof ApiError || err instanceof MissingApiConfigError) return err.message;
  return "Ocurrió un error inesperado. Intenta de nuevo.";
}
