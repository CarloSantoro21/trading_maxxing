import type { SupabaseClient } from "@supabase/supabase-js";
import { type StrategyState, toNumber } from "@/lib/format";

/**
 * Datos de la página /strategies (#58), leídos con supabase-js.
 *
 * strategies, strategy_transitions y reference_equity los lee cualquier usuario autenticado
 * (son de la capa compartida, propuesta 3.4); app_roles solo deja leer el rol propio.
 * `shadow_days_clean` es una columna calculada de PostgREST (migración reference_ledger).
 */

/** Días mínimos en shadow sin fallas para promover (igual que la API y tm_agents). */
export const MIN_SHADOW_DAYS = 7;

export interface StrategyRow {
  id: string;
  name: string;
  state: StrategyState;
  params: Record<string, unknown>;
  lab_report: Record<string, unknown> | null;
  live_metrics: Record<string, unknown>;
  state_changed_at: string;
  shadow_days_clean: number | null;
}

export interface TransitionRow {
  id: number;
  strategy_id: string;
  from_state: StrategyState;
  to_state: StrategyState;
  actor: string;
  reason: string;
  created_at: string;
}

export interface ReferenceEquityRow {
  strategy_id: string;
  started_at: string;
  day: string;
  equity: number | string;
  drawdown: number | string;
  state?: string;
}

export interface StrategiesData {
  strategies: StrategyRow[];
  transitions: TransitionRow[];
  equity: ReferenceEquityRow[];
  isAdmin: boolean;
}

export class StrategiesLoadError extends Error {
  constructor(message: string, options?: { cause?: unknown }) {
    super(message, options);
    this.name = "StrategiesLoadError";
  }
}

function check<T>(res: { data: T | null; error: unknown }, what: string): T {
  if (res.error) throw new StrategiesLoadError(`No se pudo leer ${what}.`, { cause: res.error });
  return (res.data ?? ([] as unknown as T)) as T;
}

export async function fetchStrategies(supabase: SupabaseClient, userId: string): Promise<StrategiesData> {
  const [strategiesRes, transitionsRes, equityRes, roleRes] = await Promise.all([
    supabase
      .from("strategies")
      .select("id, name, state, params, lab_report, live_metrics, state_changed_at, shadow_days_clean")
      .order("name", { ascending: true }),
    supabase
      .from("strategy_transitions")
      .select("id, strategy_id, from_state, to_state, actor, reason, created_at")
      .order("created_at", { ascending: false })
      .limit(500),
    supabase
      .from("reference_equity")
      .select("strategy_id, started_at, day, equity, drawdown, state")
      .order("day", { ascending: true })
      .limit(10000),
    supabase.from("app_roles").select("role").eq("user_id", userId).maybeSingle(),
  ]);
  const strategies = check<StrategyRow[]>(strategiesRes, "las estrategias");
  const transitions = check<TransitionRow[]>(transitionsRes, "el historial de transiciones");
  const equity = check<ReferenceEquityRow[]>(equityRes, "el portafolio de referencia");
  // Si no se puede leer el rol, la página sigue en modo lectura (la API vuelve a exigirlo).
  const role = roleRes.error ? null : (roleRes.data as { role?: string } | null)?.role;
  return {
    strategies: strategies.map((s) => ({
      ...s,
      params: s.params ?? {},
      live_metrics: s.live_metrics ?? {},
      shadow_days_clean: toNumber(s.shadow_days_clean),
    })),
    transitions,
    equity,
    isAdmin: role === "admin",
  };
}

// --- reglas de los botones -------------------------------------------------------------

export interface ActionAvailability {
  canPromote: boolean;
  /** Por qué no se puede promover (si el estado lo permitiría). */
  promoteHint: string | null;
  canRetire: boolean;
}

/**
 * Qué acciones de admin aplican a una estrategia. La API repite estas reglas (y la base,
 * las transiciones permitidas): esto solo evita ofrecer botones que fallarían.
 */
export function availableActions(s: Pick<StrategyRow, "state" | "shadow_days_clean">): ActionAvailability {
  const canRetire = s.state !== "retired";
  if (s.state === "probation") return { canPromote: true, promoteHint: null, canRetire };
  if (s.state !== "shadow") return { canPromote: false, promoteHint: null, canRetire };
  const days = s.shadow_days_clean ?? 0;
  if (days >= MIN_SHADOW_DAYS) return { canPromote: true, promoteHint: null, canRetire };
  return {
    canPromote: false,
    promoteHint: `Lleva ${days.toFixed(1)} de ${MIN_SHADOW_DAYS} días en shadow sin fallas.`,
    canRetire,
  };
}

// --- portafolio de referencia y banda del Monte Carlo ---------------------------------

export interface McBand {
  horizonDays: number;
  p5: number;
  p50: number;
  p95: number;
}

function obj(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

/** Percentiles del retorno final del Monte Carlo (formato del walk-forward del lab). */
export function mcBand(report: Record<string, unknown> | null): McBand | null {
  const mc = obj(report?.montecarlo);
  const fr = obj(mc?.final_return);
  const horizon = toNumber(mc?.horizon_days);
  const p5 = toNumber(fr?.p5);
  const p95 = toNumber(fr?.p95);
  if (!fr || !horizon || horizon <= 0 || p5 === null || p95 === null) return null;
  return { horizonDays: horizon, p5, p50: toNumber(fr.p50) ?? (p5 + p95) / 2, p95 };
}

export interface DrawdownBand {
  p5: number | null;
  p50: number | null;
  p95: number;
  p99: number | null;
}

/** Banda del drawdown máximo (mismos formatos que lee el LifecycleMonitor). */
export function drawdownBand(report: Record<string, unknown> | null): DrawdownBand | null {
  if (!report) return null;
  const dist = obj(obj(report.montecarlo)?.max_drawdown);
  const p95 = toNumber(dist?.p95);
  if (dist && p95 !== null) {
    return { p5: toNumber(dist.p5), p50: toNumber(dist.p50), p95, p99: toNumber(dist.p99) };
  }
  const flat = toNumber(report.max_drawdown_p95);
  if (flat !== null) return { p5: null, p50: null, p95: flat, p99: toNumber(report.max_drawdown_p99) };
  return null;
}

export interface ReferencePoint {
  day: string;
  t: number;
  equity: number;
  drawdown: number;
  /** [p5, p95] esperados por el Monte Carlo para ese día (rango del área). */
  band?: [number, number];
  p50?: number;
}

const DAY_MS = 86_400_000;

/** Puntos de la corrida más reciente del ledger de una estrategia (la última entrada a shadow). */
export function latestRun(rows: ReferenceEquityRow[], strategyId: string): ReferenceEquityRow[] {
  const own = rows.filter((r) => r.strategy_id === strategyId);
  if (own.length === 0) return [];
  const last = own.reduce((m, r) => (Date.parse(r.started_at) > Date.parse(m) ? r.started_at : m), own[0].started_at);
  return own.filter((r) => r.started_at === last).sort((a, b) => a.day.localeCompare(b.day));
}

/**
 * Equity de referencia con la banda esperada del Monte Carlo.
 *
 * El laboratorio guarda la distribución del retorno a `horizon_days` (un año). Para cada
 * día t de la corrida la banda es `equity_inicial × (1 + p)^(t / horizonte)` con p = p5, p50
 * y p95: una interpolación geométrica, que supone que el retorno se acumula a ritmo
 * constante. Es una aproximación (los percentiles de días intermedios no son exactamente
 * esos), suficiente para ver si la estrategia va dentro de lo que el laboratorio esperaba.
 */
export function referenceSeries(rows: ReferenceEquityRow[], band: McBand | null): ReferencePoint[] {
  const points = rows
    .map((r) => ({ day: r.day, t: Date.parse(`${r.day}T00:00:00Z`), equity: toNumber(r.equity), drawdown: toNumber(r.drawdown) ?? 0 }))
    .filter((p): p is { day: string; t: number; equity: number; drawdown: number } => p.equity !== null && !Number.isNaN(p.t));
  if (points.length === 0) return [];
  const start = points[0];
  return points.map((p) => {
    if (!band) return p;
    const frac = (p.t - start.t) / DAY_MS / band.horizonDays;
    const at = (r: number) => start.equity * Math.pow(Math.max(1 + r, 0), frac);
    return { ...p, band: [at(band.p5), at(band.p95)] as [number, number], p50: at(band.p50) };
  });
}

/** ¿El último punto está dentro de la banda? null si no hay banda. */
export function bandPosition(points: ReferencePoint[]): "below" | "inside" | "above" | null {
  const last = points[points.length - 1];
  if (!last?.band) return null;
  if (last.equity < last.band[0]) return "below";
  if (last.equity > last.band[1]) return "above";
  return "inside";
}

// --- resumen del reporte del laboratorio -------------------------------------------------

export interface LabCheck {
  name: string;
  passed: boolean;
  reason: string;
}

export interface LabSummary {
  approved: boolean | null;
  sharpe: number | null;
  cagr: number | null;
  maxDrawdown: number | null;
  trades: number | null;
  oosYears: number | null;
  start: string | null;
  end: string | null;
  checks: LabCheck[];
}

export function labSummary(report: Record<string, unknown> | null): LabSummary | null {
  if (!report) return null;
  const agg = obj(report.aggregate);
  const approval = obj(report.approval);
  const checks: LabCheck[] = Array.isArray(approval?.checks)
    ? (approval.checks as unknown[])
        .map(obj)
        .filter((c): c is Record<string, unknown> => c !== null)
        .map((c) => ({ name: String(c.name ?? ""), passed: Boolean(c.passed), reason: String(c.reason ?? c.name ?? "") }))
    : [];
  const str = (v: unknown) => (typeof v === "string" ? v : null);
  return {
    approved: typeof approval?.approved === "boolean" ? approval.approved : null,
    sharpe: toNumber(agg?.sharpe ?? report.sharpe_oos),
    cagr: toNumber(agg?.cagr),
    maxDrawdown: toNumber(agg?.max_drawdown),
    trades: toNumber(agg?.trades),
    oosYears: toNumber(agg?.oos_years),
    start: str(agg?.start),
    end: str(agg?.end),
    checks,
  };
}

/** Quién hizo una transición, en palabras. */
export function actorLabel(actor: string, currentUserId: string | undefined): string {
  if (actor === "system") return "Sistema (automático)";
  if (currentUserId && actor === currentUserId) return "Tú";
  return `Admin ${actor.slice(0, 8)}`;
}
