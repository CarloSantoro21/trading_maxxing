/**
 * Monte Carlo #3 (#38): probabilidad de alcanzar una meta antes de su fecha límite.
 * Port de tm_api.projection y tm_api.goal_projection (ver su docstring para el método).
 *
 * Diferencias con Python (por el límite de CPU de un Worker del plan gratuito):
 *   * menos simulaciones (CELL_BUDGET de 1,000,000 celdas en vez de 4,000,000);
 *   * el generador aleatorio es mulberry32 (ver montecarlo.ts): la semilla fija hace que
 *     la misma meta con los mismos datos dé la misma respuesta, aunque no la misma que
 *     daba numpy.
 */

import { type Horizon, type StrategyStats, allocate } from "./allocator";
import { type Curve, exposureForLevel, sensitivityFor, KNOWN_SENSITIVITY } from "./exposure";
import { type EquityPoint, REPORT_NAME, labEquityPoints, returnsFromEquity } from "./lab-core";
import { getProjectionReport } from "./lab-reports";
import { bootstrap, mean, percentileSorted, sorted, stdSample } from "./montecarlo";
import { type Row, all, num, placeholders } from "./db";

export const MIN_REFERENCE_DAYS = 60;
export const MIN_OBSERVATIONS = 30;
export const BLOCK_SIZE = 20;
export const SEED = 42;
export const MAX_SIMULATIONS = 2_000;
export const MIN_SIMULATIONS = 500;
export const CELL_BUDGET = 1_000_000;
export const MAX_HORIZON_DAYS = 3_653;
export const FAN_POINTS = 60;
const ANNUALIZATION = 365;
const DAY_MS = 86_400_000;

export class ProjectionError extends Error {}

type Json = Record<string, unknown>;
const obj = (v: unknown): Json => (v && typeof v === "object" && !Array.isArray(v) ? (v as Json) : {});
const finite = (v: unknown, fallback: number | null): number | null => {
  const n = typeof v === "number" ? v : typeof v === "string" && v.trim() ? Number(v) : Number.NaN;
  return Number.isFinite(n) ? n : fallback;
};

export interface StrategySeries {
  name: string;
  state: string;
  lab_report: Json | null;
  returns: EquityPoint[];
  source: "reference" | "lab_report" | "none";
  report_source: "db" | "file" | "none";
}

export interface Contribution {
  name: string;
  state: string;
  source: string;
  report_source: string;
  observations: number;
  weight: number;
  exposure: number;
  excluded: string | null;
}

export interface FanPoint {
  day: string;
  p10: number;
  p50: number;
  p90: number;
}

export interface GoalProjection {
  start_equity: number;
  target_equity: number;
  days: number;
  probability: number;
  probability_at_deadline: number;
  invested_fraction: number;
  n_simulations: number;
  block_size: number;
  seed: number;
  final_equity_p10: number;
  final_equity_p50: number;
  final_equity_p90: number;
  fan: FanPoint[];
  strategies: Contribution[];
  notes: string[];
}

export function simulationsFor(days: number): number {
  return Math.max(MIN_SIMULATIONS, Math.min(MAX_SIMULATIONS, Math.floor(CELL_BUDGET / Math.max(days, 1))));
}

/** Python round(): mitad al par. */
function roundHalfEven(x: number): number {
  const r = Math.round(x);
  return Math.abs(x % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r;
}

export function fanIndices(days: number, points = FAN_POINTS): number[] {
  if (days + 1 <= points) return Array.from({ length: days + 1 }, (_, i) => i);
  const set = new Set<number>();
  for (let i = 0; i < points; i++) set.add(roundHalfEven((i * days) / (points - 1)));
  return [...set].sort((a, b) => a - b);
}

const addDays = (day: string, n: number): string =>
  new Date(Date.parse(`${day}T00:00:00Z`) + n * DAY_MS).toISOString().slice(0, 10);

const daysBetween = (from: string, to: string): number =>
  Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / DAY_MS);

/** Alinea por fecha; si casi no se traslapan, por posición desde el final. */
function align(series: Map<string, EquityPoint[]>): Map<string, number[]> {
  const out = new Map<string, number[]>();
  if (series.size === 0) return out;
  const byDate = new Map([...series].map(([name, values]) => [name, new Map(values)]));
  let common: string[] | null = null;
  for (const m of byDate.values()) {
    const keys = [...m.keys()];
    common = common === null ? keys : common.filter((d) => m.has(d));
  }
  const days = [...new Set(common ?? [])].sort();
  if (days.length >= MIN_OBSERVATIONS) {
    for (const [name, m] of byDate) out.set(name, days.map((d) => m.get(d) as number));
    return out;
  }
  const n = Math.min(...[...series.values()].map((v) => v.length));
  for (const [name, values] of series) {
    const sortedValues = [...values].sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[1] - b[1]));
    out.set(
      name,
      sortedValues.slice(sortedValues.length - n).map(([, r]) => r),
    );
  }
  return out;
}

interface Snapshot {
  volatility: number;
  oos_sharpe: number;
  horizon: Horizon | null;
  p95: number | null;
  sensitivity: Curve | null;
}

/** StrategySnapshot.from_record: lo que el BotRunner sabe de la estrategia. */
function snapshot(name: string, report: Json | null): Snapshot {
  const r = report ?? {};
  const aggregate = obj(r.aggregate);
  const mc = obj(obj(r.montecarlo).max_drawdown);
  const p95 = finite(mc.p95 ?? r.max_drawdown_p95, null);
  const horizon = typeof r.horizon === "string" && ["short", "medium", "long"].includes(r.horizon) ? (r.horizon as Horizon) : null;
  const labName = typeof obj(r.config).strategy === "string" ? (obj(r.config).strategy as string) : name;
  const reported = Array.isArray(r.exposure_sensitivity) ? (r.exposure_sensitivity as Curve) : null;
  return {
    volatility: finite(aggregate.volatility, 0) ?? 0,
    oos_sharpe: finite(aggregate.sharpe, 0) ?? 0,
    horizon,
    p95: p95 !== null && p95 >= 0 && p95 <= 1 ? p95 : null,
    sensitivity: reported && reported.length ? reported : (KNOWN_SENSITIVITY[labName] ?? null),
  };
}

function stats(item: StrategySeries, returns: number[], snap: Snapshot): StrategyStats {
  const std = returns.length > 1 ? stdSample(returns) : 0;
  const vol = snap.volatility || std * Math.sqrt(ANNUALIZATION);
  let sharpe = snap.oos_sharpe;
  if (!sharpe) {
    const flat = finite((item.lab_report ?? {}).sharpe_oos, null);
    const seriesSharpe = std > 0 ? (mean(returns) / std) * Math.sqrt(ANNUALIZATION) : 0;
    sharpe = flat !== null && flat > 0 ? flat : seriesSharpe;
  }
  return {
    name: item.name,
    state: item.state,
    volatility: Number.isFinite(vol) ? vol : 0,
    oos_sharpe: Number.isFinite(sharpe) ? sharpe : 0,
    horizon: snap.horizon,
    max_drawdown_p95: snap.p95,
  };
}

export interface ProjectInput {
  startEquity: number;
  targetEquity: number;
  riskLevel: number;
  horizon: Horizon;
  today: string;
  deadline: string;
  strategies: StrategySeries[];
  seed?: number;
}

export function projectGoal(input: ProjectInput): GoalProjection {
  const { startEquity, targetEquity, riskLevel, horizon, today, deadline } = input;
  const seed = input.seed ?? SEED;
  const days = daysBetween(today, deadline);
  if (days <= 0) throw new ProjectionError("La fecha límite de la meta ya pasó");
  if (days > MAX_HORIZON_DAYS) throw new ProjectionError("La fecha límite está a más de 10 años; no se proyecta");
  if (!Number.isFinite(startEquity) || startEquity <= 0) throw new ProjectionError("El equity actual debe ser mayor que 0");

  const notes: string[] = [];
  const contributions: Contribution[] = [];
  const usable = new Map<string, StrategySeries>();
  for (const item of input.strategies) {
    if (item.returns.length < MIN_OBSERVATIONS) {
      contributions.push({
        name: item.name,
        state: item.state,
        source: item.source,
        report_source: item.report_source,
        observations: item.returns.length,
        weight: 0,
        exposure: 0,
        excluded: `sin historial suficiente (${item.returns.length} días; se requieren ${MIN_OBSERVATIONS})`,
      });
    } else {
      usable.set(item.name, item);
    }
  }

  const aligned = align(new Map([...usable].map(([n, i]) => [n, i.returns])));
  const snaps = new Map<string, Snapshot>();
  const allStats: StrategyStats[] = [];
  for (const [name, item] of usable) {
    const snap = snapshot(name, item.lab_report);
    snaps.set(name, snap);
    allStats.push(stats(item, aligned.get(name) ?? [], snap));
  }
  const allocation = allocate(Math.round(startEquity * 100) / 100, horizon, allStats);
  const length = aligned.size ? (aligned.values().next().value as number[]).length : 0;
  const combined = new Float64Array(length);
  let invested = 0;
  for (const [name, item] of usable) {
    const snap = snaps.get(name)!;
    const weight = allocation.weights[name] ?? 0;
    const exposure = exposureForLevel(riskLevel, snap.p95, sensitivityFor(name, snap.sensitivity));
    let excluded = allocation.excluded[name] ?? null;
    if (excluded === null && exposure <= 0) excluded = "sin drawdown p95 del Monte Carlo: exposición 0";
    const series = aligned.get(name) ?? [];
    if (excluded === null) {
      for (let i = 0; i < combined.length; i++) combined[i] = (combined[i] as number) + weight * exposure * (series[i] as number);
      invested += weight * exposure;
    }
    contributions.push({
      name,
      state: item.state,
      source: item.source,
      report_source: item.report_source,
      observations: series.length,
      weight: excluded === null ? weight : 0,
      exposure,
      excluded,
    });
  }
  contributions.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));

  const indices = fanIndices(days);
  const daysOut = indices.map((i) => addDays(today, i));
  if (invested <= 0 || combined.length === 0) {
    notes.push("Ninguna estrategia recibe capital con tu nivel de riesgo: el equity se queda en USDT y no cambia.");
    const reached = startEquity >= targetEquity ? 1 : 0;
    return {
      start_equity: startEquity,
      target_equity: targetEquity,
      days,
      probability: reached,
      probability_at_deadline: reached,
      invested_fraction: 0,
      n_simulations: 0,
      block_size: 0,
      seed,
      final_equity_p10: startEquity,
      final_equity_p50: startEquity,
      final_equity_p90: startEquity,
      fan: daysOut.map((day) => ({ day, p10: startEquity, p50: startEquity, p90: startEquity })),
      strategies: contributions,
      notes,
    };
  }

  if (contributions.some((c) => c.excluded === null && c.source === "lab_report")) {
    notes.push(
      "Al menos una estrategia aún no tiene historia suficiente en su portafolio de referencia: se usaron sus retornos out-of-sample del laboratorio.",
    );
  }
  const nSim = simulationsFor(days);
  const block = Math.min(BLOCK_SIZE, combined.length);
  const result = bootstrap(combined, {
    nSimulations: nSim,
    seed,
    blockSize: block,
    horizon: days,
    initialCapital: startEquity,
    fanIndices: indices,
  });
  let touched = 0;
  let atDeadline = 0;
  const finals = new Float64Array(nSim);
  for (let s = 0; s < nSim; s++) {
    if ((result.peakGrowth[s] as number) * startEquity >= targetEquity) touched++;
    const final = startEquity * (1 + (result.finalReturns[s] as number));
    finals[s] = final;
    if (final >= targetEquity) atDeadline++;
  }
  const finalSorted = sorted(finals);
  return {
    start_equity: startEquity,
    target_equity: targetEquity,
    days,
    probability: touched / nSim,
    probability_at_deadline: atDeadline / nSim,
    invested_fraction: invested,
    n_simulations: nSim,
    block_size: block,
    seed,
    final_equity_p10: percentileSorted(finalSorted, 10),
    final_equity_p50: percentileSorted(finalSorted, 50),
    final_equity_p90: percentileSorted(finalSorted, 90),
    fan: indices.map((_, k) => {
      const values = sorted(result.fan[k]!);
      return {
        day: daysOut[k]!,
        p10: percentileSorted(values, 10),
        p50: percentileSorted(values, 50),
        p90: percentileSorted(values, 90),
      };
    }),
    strategies: contributions,
    notes,
  };
}

// --- lectura de datos (goal_projection.py) ---------------------------------------------

const hasSeries = (report: Json | null): boolean =>
  Boolean(report && report.aggregate && labEquityPoints(report).length > 0);

/** El reporte de la base si está completo; si no, el archivo del laboratorio. */
export function effectiveReport(row: Row): [Json | null, "db" | "file" | "none"] {
  const report = (row.lab_report as Json | null) ?? null;
  if (hasSeries(report)) return [report, "db"];
  const labName = String(obj(obj(report).config).strategy ?? row.name);
  if (REPORT_NAME.test(labName)) {
    const file = getProjectionReport(labName);
    if (file) return [file, "file"];
  }
  return [report, report ? "db" : "none"];
}

/** Equity actual del perfil: la última foto de cada bot; sin fotos, el capital. */
export async function currentEquity(db: D1Database, profile: Row): Promise<number> {
  const rows = await all<{ equity: number }>(
    db
      .prepare(
        `SELECT es.equity FROM equity_snapshots es
         JOIN bots b ON b.id = es.bot_id
         WHERE b.profile_id = ?
           AND es.ts = (SELECT MAX(ts) FROM equity_snapshots WHERE bot_id = es.bot_id)`,
      )
      .bind(profile.id as string),
  );
  if (rows.length === 0) return num(profile.capital);
  return rows.reduce((acc, r) => acc + num(r.equity), 0);
}

export async function loadStrategySeries(db: D1Database): Promise<StrategySeries[]> {
  const strategies = await all<Row>(
    db.prepare("SELECT id, name, state, lab_report FROM strategies WHERE state IN ('active', 'probation') ORDER BY name"),
  );
  const reference = new Map<string, EquityPoint[]>();
  if (strategies.length > 0) {
    const ids = strategies.map((s) => s.id as string);
    const rows = await all<{ strategy_id: string; started_at: string; day: string; equity: number }>(
      db
        .prepare(
          `SELECT strategy_id, started_at, day, equity FROM reference_equity
           WHERE strategy_id IN (${placeholders(ids.length)}) ORDER BY day`,
        )
        .bind(...ids),
    );
    const latest = new Map<string, string>();
    for (const r of rows) if ((latest.get(r.strategy_id) ?? "") < r.started_at) latest.set(r.strategy_id, r.started_at);
    for (const r of rows) {
      if (r.started_at !== latest.get(r.strategy_id)) continue;
      const list = reference.get(r.strategy_id) ?? [];
      list.push([r.day.slice(0, 10), num(r.equity)]);
      reference.set(r.strategy_id, list);
    }
  }
  return strategies.map((row) => {
    const parsed: Row = { ...row, lab_report: typeof row.lab_report === "string" ? (JSON.parse(row.lab_report) as Json) : null };
    const [report, reportSource] = effectiveReport(parsed);
    const refReturns = returnsFromEquity(reference.get(row.id as string) ?? []);
    let returns: EquityPoint[];
    let source: StrategySeries["source"];
    if (refReturns.length >= MIN_REFERENCE_DAYS) {
      returns = refReturns;
      source = "reference";
    } else {
      returns = returnsFromEquity(labEquityPoints(report));
      source = returns.length ? "lab_report" : "none";
    }
    return { name: String(row.name), state: String(row.state), lab_report: report, returns, source, report_source: reportSource };
  });
}
