/**
 * Cliente mínimo de services/api desde el navegador.
 *
 * El sitio es estático: cada llamada sale del cliente con el `access_token` de Supabase Auth
 * en `Authorization`. La API lo reenvía a PostgREST, así que RLS decide qué ve cada quien.
 * La URL base sale de NEXT_PUBLIC_API_URL (por omisión, la api local en el puerto 8000).
 */

export const DEFAULT_API_URL = "http://localhost:8000";

export function apiBaseUrl(): string {
  // Next.js reemplaza la referencia en tiempo de build; debe escribirse literal.
  const url = process.env.NEXT_PUBLIC_API_URL;
  return (url && url.trim() ? url.trim() : DEFAULT_API_URL).replace(/\/+$/, "");
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Mensaje legible del `detail` de FastAPI (texto, objeto o lista de errores de validación). */
export function detailMessage(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : null))
      .filter((m): m is string => Boolean(m));
    return parts.length ? parts.join("; ") : null;
  }
  if (detail && typeof detail === "object" && "message" in detail) {
    return String((detail as { message: unknown }).message);
  }
  return null;
}

export type Fetcher = typeof fetch;

export async function apiFetch<T>(
  path: string,
  token: string,
  init: RequestInit = {},
  fetcher: Fetcher = fetch,
): Promise<T> {
  let response: Response;
  try {
    response = await fetcher(`${apiBaseUrl()}${path}`, {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...init.headers,
        Authorization: `Bearer ${token}`,
      },
    });
  } catch {
    throw new ApiError(
      `No se pudo conectar con la API en ${apiBaseUrl()}. Revisa que esté corriendo (make up).`,
      0,
    );
  }
  if (!response.ok) {
    let message: string | null = null;
    try {
      message = detailMessage((await response.json())?.detail);
    } catch {
      message = null;
    }
    throw new ApiError(message ?? `La API respondió ${response.status}`, response.status);
  }
  return (await response.json()) as T;
}
