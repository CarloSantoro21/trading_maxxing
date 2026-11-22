/**
 * Logs estructurados en JSON (una línea por evento).
 *
 * Workers Observability indexa los campos de un console.log con JSON, así que en el
 * dashboard se puede filtrar por request_id, user, route o status.
 */

export type Level = "debug" | "info" | "warn" | "error";

export function log(level: Level, msg: string, fields: Record<string, unknown> = {}): void {
  const line = JSON.stringify({ level, msg, ts: new Date().toISOString(), ...fields });
  if (level === "error") console.error(line);
  else if (level === "warn") console.warn(line);
  else console.log(line);
}

/** Serializa una excepción para el log (nombre, mensaje y stack). */
export function errorFields(err: unknown): Record<string, unknown> {
  if (err instanceof Error) {
    return { error_name: err.name, error_message: err.message, stack: err.stack };
  }
  return { error_message: String(err) };
}
