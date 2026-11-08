import { z } from "zod";

/**
 * Validación del wizard de onboarding (3 pasos: capital, nivel de riesgo y metas).
 *
 * Con la capa compartida (propuesta, sección 3.4) el usuario solo elige capital, nivel de
 * riesgo y metas. Siempre se operan BTCUSDT y ETHUSDT y el horizonte ya no se captura.
 *
 * Las reglas reflejan supabase/migrations/:
 *   - profiles.capital >= 500 (MIN_CAPITAL)
 *   - profiles.risk_level entre 1 y 5; profiles.max_drawdown lo deriva un trigger del nivel
 *   - goals.target_return > 0 (en la UI, porcentaje)
 *   - goals.deadline es una fecha (aquí además se exige que sea futura)
 * Si cambian los checks de la base, hay que cambiar este archivo.
 */

/** Capital mínimo en USDT de testnet (igual que tm_agents.risk.MIN_CAPITAL). */
export const MIN_CAPITAL = 500;

export interface RiskLevelSpec {
  name: string;
  /** Caída máxima tolerada desde el máximo de la cuenta; al llegar ahí el bot se detiene. */
  maxDrawdownPct: number;
  /** Lo que se pierde de la cuenta si una operación toca su stop. */
  riskPerTradePct: number;
  /** Parte de la cuenta que puede estar invertida a la vez; el resto queda en USDT. */
  maxExposurePct: number;
  /** Explicación en lenguaje simple. */
  summary: string;
}

/**
 * Copia de tm_agents.risk.RISK_TABLE. services/agents/tests/test_risk_levels_sync.py falla
 * si los valores dejan de coincidir; no cambies el orden de los campos.
 */
export const RISK_TABLE = {
  1: {
    name: "Conservador",
    maxDrawdownPct: 10,
    riskPerTradePct: 0.25,
    maxExposurePct: 20,
    summary: "Prefieres dormir tranquilo: pocas operaciones pequeñas y la mayor parte en USDT.",
  },
  2: {
    name: "Prudente",
    maxDrawdownPct: 15,
    riskPerTradePct: 0.5,
    maxExposurePct: 35,
    summary: "Aceptas algo de movimiento a cambio de un poco más de rendimiento.",
  },
  3: {
    name: "Moderado",
    maxDrawdownPct: 20,
    riskPerTradePct: 1,
    maxExposurePct: 50,
    summary: "Un punto medio: hasta la mitad de la cuenta invertida y caídas moderadas.",
  },
  4: {
    name: "Decidido",
    maxDrawdownPct: 30,
    riskPerTradePct: 1.5,
    maxExposurePct: 70,
    summary: "Buscas más rendimiento y toleras caídas fuertes en el camino.",
  },
  5: {
    name: "Agresivo",
    maxDrawdownPct: 40,
    riskPerTradePct: 2,
    maxExposurePct: 90,
    summary: "Casi toda la cuenta puede estar invertida; las caídas pueden ser grandes.",
  },
} as const satisfies Record<number, RiskLevelSpec>;

export type RiskLevel = keyof typeof RISK_TABLE;
export const RISK_LEVELS = [1, 2, 3, 4, 5] as const satisfies readonly RiskLevel[];

export function isRiskLevel(value: number): value is RiskLevel {
  return (RISK_LEVELS as readonly number[]).includes(value);
}

/** Caída máxima en USDT para un capital y un nivel: lo que se pierde antes del paro. */
export function maxLossUsd(capital: number, level: RiskLevel): number {
  return Math.round(capital * RISK_TABLE[level].maxDrawdownPct) / 100;
}

/** Monto en USDT con separador de miles y hasta 2 decimales (p. ej. "1,234.5"). */
export function formatUsd(amount: number): string {
  return amount.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

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

/** Capital capturado como número válido, o null si el texto no lo es. */
export function parseCapital(value: string): number | null {
  const n = toNumber(value);
  return typeof n === "number" && Number.isFinite(n) && n > 0 ? n : null;
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
  capital: numberField("Escribe el capital inicial.").refine((v) => v >= MIN_CAPITAL, {
    message: `El capital mínimo es de ${MIN_CAPITAL} USDT.`,
  }),
  riskLevel: numberField("Elige un nivel de riesgo.").refine(
    (v) => Number.isInteger(v) && v >= 1 && v <= 5,
    { message: "El nivel de riesgo debe ser un entero entre 1 y 5." },
  ),
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
  riskLevel: string;
  goals: GoalFormState[];
}

export const EMPTY_FORM: OnboardingFormState = {
  capital: "",
  riskLevel: "3",
  goals: [{ targetReturnPct: "", deadline: "" }],
};

export type FieldErrors = Record<string, string>;

/** Pasos del wizard y los campos que valida cada uno. */
export const STEPS = [
  { id: "capital", title: "Capital", fields: ["capital"] },
  { id: "risk", title: "Nivel de riesgo", fields: ["riskLevel"] },
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
