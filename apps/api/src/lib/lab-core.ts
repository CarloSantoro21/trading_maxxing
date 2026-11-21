/**
 * Reportes del laboratorio de walk-forward (port de tm_api.lab_reports).
 *
 * Funciones puras: las usa el script de build (scripts/build-lab-reports.ts), que lee
 * docs/estrategias/reportes/*.json y genera src/generated/lab-reports.json, y el Worker
 * para la proyección de metas. En el Worker no hay sistema de archivos, por eso el JSON
 * se arma en build y se empaqueta dentro del Worker.
 */

import { bootstrap, histogram, percentile, type HistogramBin } from "./montecarlo";

/** Nombre de un reporte (el del archivo sin .json). */
export const REPORT_NAME = /^[a-z][a-z0-9_]{0,63}$/;
export const HISTOGRAM_BINS = 30;
export const ONE_YEAR_DAYS = 365;

type Json = Record<string, unknown>;
export type EquityPoint = [day: string, value: number];

const obj = (v: unknown): Json => (v && typeof v === "object" && !Array.isArray(v) ? (v as Json) : {});
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const strOrNull = (v: unknown): string | null => (typeof v === "string" ? v : v == null ? null : String(v));
const numOrNull = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

export interface LabSummary {
  name: string;
  strategy: string;
  symbols: string[];
  oos_start: string | null;
  oos_end: string | null;
  aggregate: Json;
  buy_and_hold: Json;
  max_drawdown_p95: number | null;
  approved: boolean;
  reasons: string[];
}

export interface DrawdownHistogram {
  bins: HistogramBin[];
  p95: number;
  p95_recomputed: number;
  p50: number;
  n_simulations: number;
  block_size: number;
  seed: number;
  horizon_days: number;
  limit: number | null;
}

export interface LabDetail extends LabSummary {
  config: Json;
  per_symbol: Json;
  parameter_stability: Json;
  montecarlo: Json;
  checks: Json[];
  exposure_by_risk_level: Record<string, number>;
  windows: Json[];
  equity: { day: string; strategy: number; buy_and_hold: number | null }[];
  drawdown_histogram: DrawdownHistogram | null;
}

export function summary(name: string, report: Json): LabSummary {
  const config = obj(report.config);
  const approval = obj(report.approval);
  const mc = obj(obj(report.montecarlo).max_drawdown);
  return {
    name,
    strategy: typeof config.strategy === "string" ? config.strategy : name,
    symbols: arr(config.symbols).map(String),
    oos_start: strOrNull(config.oos_start),
    oos_end: strOrNull(config.oos_end),
    aggregate: obj(report.aggregate),
    buy_and_hold: obj(report.buy_and_hold),
    max_drawdown_p95: numOrNull(mc.p95),
    approved: approval.approved === true,
    reasons: arr(approval.reasons).map(String),
  };
}

function pairs(raw: unknown): EquityPoint[] {
  const out: EquityPoint[] = [];
  for (const item of arr(raw)) {
    if (!Array.isArray(item) || item.length < 2) continue;
    const value = Number(item[1]);
    if (!Number.isFinite(value)) continue;
    out.push([String(item[0]).slice(0, 10), value]);
  }
  return out;
}

/** Curva diaria {day, strategy, buy_and_hold} (comprar y mantener puede faltar). */
export function equityCurve(report: Json): LabDetail["equity"] {
  const daily = obj(report.equity_daily);
  const hold = new Map(pairs(daily.buy_and_hold));
  return pairs(daily.strategy).map(([day, value]) => ({ day, strategy: value, buy_and_hold: hold.get(day) ?? null }));
}

export function windows(report: Json): Json[] {
  return arr(report.windows).map((raw) => {
    const w = obj(raw);
    const oos = obj(w.oos);
    const hold = obj(w.buy_and_hold);
    return {
      label: w.label ?? null,
      is_start: w.is_start ?? null,
      is_end: w.is_end ?? null,
      oos_start: w.oos_start ?? null,
      oos_end: w.oos_end ?? null,
      params_label: w.params_label ?? null,
      is_sharpe: w.is_sharpe ?? null,
      oos_return: oos.total_return ?? null,
      oos_sharpe: oos.sharpe ?? null,
      oos_max_drawdown: oos.max_drawdown ?? null,
      hold_return: hold.total_return ?? null,
      trades: w.trades ?? null,
    };
  });
}

/** Curva equity_daily.strategy de un reporte (vacía si no la trae). */
export function labEquityPoints(report: Json | null | undefined): EquityPoint[] {
  if (!report) return [];
  return pairs(obj(report.equity_daily).strategy);
}

/** Retornos diarios (día, equity_t / equity_{t-1} - 1); ignora valores no positivos. */
export function returnsFromEquity(points: readonly EquityPoint[]): EquityPoint[] {
  const clean = points
    .filter(([, v]) => Number.isFinite(v) && v > 0)
    .slice()
    .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[1] - b[1]));
  const out: EquityPoint[] = [];
  for (let i = 1; i < clean.length; i++) {
    const prev = clean[i - 1] as EquityPoint;
    const cur = clean[i] as EquityPoint;
    out.push([cur[0], cur[1] / prev[1] - 1]);
  }
  return out;
}

/**
 * Histograma del drawdown máximo: repite el block bootstrap del laboratorio con los
 * parámetros del reporte. `p95` es el del reporte; `p95_recomputed`, el de estas muestras
 * (difiere un poco del de Python porque el generador aleatorio es otro).
 */
export function drawdownHistogram(report: Json): DrawdownHistogram | null {
  const returns = returnsFromEquity(labEquityPoints(report)).map(([, r]) => r);
  const mc = obj(report.montecarlo);
  const config = obj(report.config);
  const block = Math.trunc(Number(mc.block_size ?? config.block_size ?? 20)) || 20;
  if (returns.length < Math.max(block, 2)) return null;
  const nSim = Math.trunc(Number(mc.n_simulations ?? config.n_simulations ?? 10_000)) || 10_000;
  const seed = Math.trunc(Number(config.seed ?? 42));
  const horizon = Math.trunc(Number(mc.horizon_days ?? ONE_YEAR_DAYS)) || ONE_YEAR_DAYS;
  const result = bootstrap(returns, { nSimulations: nSim, seed, blockSize: block, horizon });
  const samples = result.maxDrawdowns;
  let max = 0;
  for (const v of samples) if (v > max) max = v;
  const p95 = percentile(samples, 95);
  const reported = numOrNull(obj(mc.max_drawdown).p95);
  const limit = numOrNull(obj(obj(report.approval).criteria).max_drawdown_p95);
  return {
    bins: histogram(samples, HISTOGRAM_BINS, 0, max),
    p95: reported ?? p95,
    p95_recomputed: p95,
    p50: percentile(samples, 50),
    n_simulations: nSim,
    block_size: block,
    seed,
    horizon_days: horizon,
    limit,
  };
}

export function detail(name: string, report: Json): LabDetail {
  const approval = obj(report.approval);
  const exposure: Record<string, number> = {};
  for (const [k, v] of Object.entries(obj(approval.exposure_by_risk_level))) {
    if (typeof v === "number") exposure[k] = v;
  }
  return {
    ...summary(name, report),
    config: obj(report.config),
    per_symbol: obj(report.per_symbol),
    parameter_stability: obj(report.parameter_stability),
    montecarlo: obj(report.montecarlo),
    checks: arr(approval.checks).map(obj),
    exposure_by_risk_level: exposure,
    windows: windows(report),
    equity: equityCurve(report),
    drawdown_histogram: drawdownHistogram(report),
  };
}

/** Lo mínimo del reporte que necesita la proyección de metas (sin la curva, que ya está en detail). */
export function projectionFields(report: Json): Json {
  const out: Json = {
    config: obj(report.config),
    aggregate: obj(report.aggregate),
    montecarlo: obj(report.montecarlo),
  };
  if (report.horizon !== undefined) out.horizon = report.horizon;
  if (report.exposure_sensitivity !== undefined) out.exposure_sensitivity = report.exposure_sensitivity;
  return out;
}

export interface LabBundle {
  generated_at: string;
  reports: Record<string, { detail: LabDetail; projection: Json }>;
}
