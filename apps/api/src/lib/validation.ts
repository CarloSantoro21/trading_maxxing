/**
 * Validación con zod y esquemas reutilizables.
 *
 * Los errores salen en el formato de FastAPI (422 con `detail: [{type, loc, msg}]`) para
 * que el frontend los muestre igual que antes.
 */

import type { Context } from "hono";
import { z } from "zod";
import { ApiError } from "./errors";

export type Where = "body" | "query" | "path";

export function parse<T extends z.ZodType>(schema: T, data: unknown, where: Where): z.output<T> {
  const result = schema.safeParse(data);
  if (!result.success) {
    throw new ApiError(
      422,
      result.error.issues.map((issue) => ({
        type: issue.code,
        loc: [where, ...issue.path.map((p) => (typeof p === "symbol" ? String(p) : p))],
        msg: issue.message,
      })),
    );
  }
  return result.data;
}

/** Lee el cuerpo JSON; `undefined` si viene vacío. */
export async function readJson(c: Context): Promise<unknown> {
  const text = await c.req.text();
  if (!text.trim()) return undefined;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new ApiError(422, [{ type: "json_invalid", loc: ["body"], msg: "El cuerpo no es JSON válido" }]);
  }
}

export async function body<T extends z.ZodType>(c: Context, schema: T): Promise<z.output<T>> {
  return parse(schema, await readJson(c), "body");
}

export function query<T extends z.ZodType>(c: Context, schema: T): z.output<T> {
  return parse(schema, c.req.query(), "query");
}

// --- esquemas comunes ----------------------------------------------------------------

/** UUID con cualquier variante (los del seed, como 1111..., no son RFC 4122 estrictos). */
export const Uuid = z.guid({ message: "debe ser un UUID" });

export function pathUuid(c: Context, name: string): string {
  return parse(Uuid, c.req.param(name), "path");
}

const NUMERIC = /^[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?$/;

/** Número que también acepta texto ("10000"), como Decimal en pydantic. */
export const Num = z
  .union([z.number(), z.string().trim().regex(NUMERIC, "debe ser un número").transform(Number)])
  .pipe(z.number());

export const IsoDate = z
  .string()
  .regex(/^\d{4}-\d{2}-\d{2}$/, "debe ser una fecha YYYY-MM-DD")
  .refine((s) => !Number.isNaN(Date.parse(`${s}T00:00:00Z`)) && new Date(`${s}T00:00:00Z`).toISOString().startsWith(s), {
    message: "fecha inválida",
  });

/** Fecha y hora ISO 8601 con zona; se normaliza a UTC con milisegundos. */
export const IsoDateTime = z
  .string()
  .min(10)
  .transform((s, ctx) => {
    const ms = Date.parse(normalizeFraction(s));
    if (Number.isNaN(ms)) {
      ctx.addIssue({ code: "custom", message: "debe ser fecha y hora ISO 8601" });
      return z.NEVER;
    }
    return new Date(ms).toISOString();
  });

/** Python manda microsegundos (6 decimales); Date.parse solo garantiza milisegundos. */
function normalizeFraction(s: string): string {
  return s.replace(/(\.\d{3})\d+/, "$1");
}

export const JsonObject = z.record(z.string(), z.unknown());

export const Symbol = z.enum(["BTCUSDT", "ETHUSDT"]);
export const BotStatus = z.enum(["stopped", "running", "paused", "killed"]);
export const BacktestStatus = z.enum(["queued", "running", "done", "failed"]);
export const StrategyState = z.enum(["research", "lab", "shadow", "active", "probation", "retired"]);
export const Horizon = z.enum(["short", "medium", "long"]);
export const Timeframe = z.enum(["1m", "15m", "30m", "4h", "1d"]);

export type StrategyStateT = z.infer<typeof StrategyState>;

/** Entero en la query string (`?limit=50`). */
export function intQuery(min: number, max: number, fallback: number) {
  return z
    .string()
    .regex(/^\d+$/, "debe ser un entero")
    .transform(Number)
    .pipe(z.number().int().min(min).max(max))
    .optional()
    .transform((v) => v ?? fallback);
}

/** PATCH: al menos un campo y ninguno en null. */
export function requireSomeField(value: Record<string, unknown>, ctx: z.RefinementCtx): void {
  const keys = Object.keys(value);
  if (keys.length === 0) {
    ctx.addIssue({ code: "custom", message: "envía al menos un campo para actualizar" });
  }
  for (const key of keys) {
    if (value[key] === null) {
      ctx.addIssue({ code: "custom", message: `${key} no puede ser null`, path: [key] });
    }
  }
}
