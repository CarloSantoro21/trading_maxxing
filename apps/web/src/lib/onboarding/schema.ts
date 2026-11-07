import { z } from "zod";

/**
 * Validación del wizard de onboarding.
 *
 * Las reglas reflejan los checks de supabase/migrations/20261107000100_tables.sql:
 *   - profiles.capital > 0
 *   - profiles.risk_level entre 1 y 5
 *   - profiles.max_drawdown > 0 y < 1 (en la UI se captura como porcentaje: 0 < x < 100)
 *   - profiles.instruments no vacío y subconjunto de {BTCUSDT, ETHUSDT}
 *   - goals.target_return > 0 (en la UI, porcentaje)
 *   - goals.deadline es una fecha (aquí además se exige que sea futura)
 * Si cambian los checks de la base, hay que cambiar este archivo.
 */

export const HORIZONS = ["short", "medium", "long"] as const;
export type Horizon = (typeof HORIZONS)[number];

export const HORIZON_LABELS: Record<Horizon, string> = {
  short: "Corto plazo",
  medium: "Mediano plazo",
  long: "Largo plazo",
};

export const SYMBOLS = ["BTCUSDT", "ETHUSDT"] as const;
export type SupportedSymbol = (typeof SYMBOLS)[number];

export const SYMBOL_LABELS: Record<SupportedSymbol, string> = {
  BTCUSDT: "BTC/USDT",
  ETHUSDT: "ETH/USDT",
};

export const RISK_LEVELS = [1, 2, 3, 4, 5] as const;

export const RISK_LABELS: Record<number, string> = {
  1: "Muy conservador",
  2: "Conservador",
  3: "Moderado",
  4: "Agresivo",
  5: "Muy agresivo",
};

export const MAX_GOALS = 5;

/** Fecha local de hoy en formato YYYY-MM-DD. */
export function todayIso(now: Date = new Date()): string {
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

/** Convierte el texto de un input numérico; vacío o inválido da NaN. */
function toNumber(value: unknown): unknown {
  if (typeof value === "number") return value;
  if (typeof value !== "string") return value;
  const trimmed = value.trim().replace(",", ".");
  if (trimmed === "") return Number.NaN;
  return Number(trimmed);
}

const numberField = (requiredMessage: string) =>
  z.preprocess(toNumber, z.number({ error: requiredMessage }).refine(Number.isFinite, requiredMessage));

export const goalSchema = z.object({
  targetReturnPct: numberField("Escribe el retorno objetivo.").refine((v) => v > 0, {
    message: "El retorno objetivo debe ser mayor que 0 %.",
  }),
  deadline: z
    .string()
    .regex(/^\d{4}-\d{2}-\d{2}$/, { message: "Elige una fecha límite." })
    .refine((v) => !Number.isNaN(Date.parse(v)), { message: "La fecha no es válida." })
    .refine((v) => v > todayIso(), { message: "La fecha límite debe ser posterior a hoy." }),
});

export const onboardingSchema = z.object({
  capital: numberField("Escribe el capital inicial.").refine((v) => v > 0, {
    message: "El capital debe ser mayor que 0.",
  }),
  horizon: z.enum(HORIZONS, { error: "Elige un horizonte de inversión." }),
  riskLevel: numberField("Elige un nivel de riesgo.").refine(
    (v) => Number.isInteger(v) && v >= 1 && v <= 5,
    { message: "El nivel de riesgo debe ser un entero entre 1 y 5." },
  ),
  maxDrawdownPct: numberField("Escribe el drawdown máximo.").refine((v) => v > 0 && v < 100, {
    message: "El drawdown máximo debe ser mayor que 0 % y menor que 100 %.",
  }),
  instruments: z
    .array(z.enum(SYMBOLS, { error: "Instrumento no soportado." }))
    .min(1, { message: "Elige al menos un instrumento." })
    .refine((v) => new Set(v).size === v.length, { message: "Hay instrumentos repetidos." }),
  goals: z
    .array(goalSchema)
    .min(1, { message: "Agrega al menos una meta." })
    .max(MAX_GOALS, { message: `Puedes registrar hasta ${MAX_GOALS} metas.` }),
});

export type OnboardingValues = z.infer<typeof onboardingSchema>;

/** Estado del formulario tal como lo capturan los inputs (texto). */
export interface GoalFormState {
  targetReturnPct: string;
  deadline: string;
}

export interface OnboardingFormState {
  capital: string;
  horizon: Horizon | "";
  riskLevel: string;
  maxDrawdownPct: string;
  instruments: SupportedSymbol[];
  goals: GoalFormState[];
}

export const EMPTY_FORM: OnboardingFormState = {
  capital: "",
  horizon: "",
  riskLevel: "3",
  maxDrawdownPct: "15",
  instruments: ["BTCUSDT", "ETHUSDT"],
  goals: [{ targetReturnPct: "", deadline: "" }],
};

export type FieldErrors = Record<string, string>;

/** Pasos del wizard y los campos que valida cada uno. */
export const STEPS = [
  { id: "capital", title: "Capital y horizonte", fields: ["capital", "horizon"] },
  { id: "risk", title: "Riesgo", fields: ["riskLevel", "maxDrawdownPct"] },
  { id: "instruments", title: "Instrumentos", fields: ["instruments"] },
  { id: "goals", title: "Metas", fields: ["goals"] },
] as const;

/**
 * Valida el formulario completo. Devuelve los valores ya convertidos o un mapa de
 * errores cuya llave es la ruta del campo, p. ej. "capital" o "goals.0.deadline".
 */
export function validateOnboarding(
  form: OnboardingFormState,
): { success: true; data: OnboardingValues } | { success: false; errors: FieldErrors } {
  const result = onboardingSchema.safeParse(form);
  if (result.success) return { success: true, data: result.data };
  const errors: FieldErrors = {};
  for (const issue of result.error.issues) {
    const key = issue.path.map(String).join(".");
    if (!(key in errors)) errors[key] = issue.message;
  }
  return { success: false, errors };
}

/** Errores que pertenecen a los campos de un paso del wizard. */
export function errorsForStep(errors: FieldErrors, stepIndex: number): FieldErrors {
  const fields: readonly string[] = STEPS[stepIndex].fields;
  return Object.fromEntries(
    Object.entries(errors).filter(([key]) => fields.includes(key.split(".")[0])),
  );
}

/** Convierte un porcentaje de la UI a la fracción que guarda la base (15 -> 0.15). */
export function pctToFraction(pct: number): number {
  return Number((pct / 100).toFixed(8));
}

/** Convierte una fracción de la base a porcentaje para la UI (0.15 -> 15). */
export function fractionToPct(fraction: number): number {
  return Number((fraction * 100).toFixed(6));
}
