/**
 * Errores HTTP con el mismo formato que FastAPI: `{"detail": ...}`.
 *
 * `detail` es un texto o, en errores de validación (422), una lista de
 * `{type, loc, msg}`. `code` es opcional y sirve para que el cliente Python del motor
 * distinga casos (por ejemplo `stale_state` o `strategy_exists`).
 */

import type { ContentfulStatusCode } from "hono/utils/http-status";

export class ApiError extends Error {
  constructor(
    readonly status: ContentfulStatusCode,
    readonly detail: unknown,
    readonly code?: string,
  ) {
    super(typeof detail === "string" ? detail : `HTTP ${status}`);
    this.name = "ApiError";
  }

  body(): Record<string, unknown> {
    return this.code ? { detail: this.detail, code: this.code } : { detail: this.detail };
  }
}

export const notFound = (resource: string): ApiError => new ApiError(404, `${resource} no encontrado`);

export const forbidden = (detail: string): ApiError => new ApiError(403, detail);

export const conflict = (detail: string, code?: string): ApiError => new ApiError(409, detail, code);

/** Errores de SQLite/D1 que corresponden a datos inválidos del cliente. */
export function fromDbError(err: unknown): ApiError | null {
  const message = err instanceof Error ? err.message : String(err);
  if (message.includes("stale_state")) {
    return conflict("El recurso cambió mientras se procesaba; reintenta", "stale_state");
  }
  if (message.includes("UNIQUE constraint failed")) {
    return conflict("Ya existe un registro con esos datos", "unique_violation");
  }
  if (message.includes("CHECK constraint failed")) {
    return new ApiError(422, "Los datos no cumplen una regla de la base", "check_violation");
  }
  if (message.includes("FOREIGN KEY constraint failed")) {
    return new ApiError(422, "El registro referenciado no existe", "foreign_key_violation");
  }
  if (message.includes("NOT NULL constraint failed")) {
    return new ApiError(422, "Falta un campo obligatorio", "not_null_violation");
  }
  return null;
}
