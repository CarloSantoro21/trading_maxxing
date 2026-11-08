import { z } from "zod";
import { apiFetch, type Fetcher } from "@/lib/tmApi";

/** Tipos de GET /lab/reports, GET /lab/reports/{name} y /backtests (services/api). */

export type Metrics = Partial<
  Record<
    | "total_return"
    | "cagr"
    | "sharpe"
    | "volatility"
    | "max_drawdown"
    | "trades"
    | "win_rate"
    | "oos_years"
    | "exposure"
    | "final_equity",
    number
  >
>;

export interface LabReportSummary {
  name: string;
  strategy: string;
  symbols: string[];
  oos_start: string | null;
  oos_end: string | null;
  aggregate: Metrics;
  buy_and_hold: Metrics;
  max_drawdown_p95: number | null;
  approved: boolean;
  reasons: string[];
}

export interface LabWindow {
  label: string | null;
  is_start: string | null;
  is_end: string | null;
  oos_start: string | null;
  oos_end: string | null;
  params_label: string | null;
  is_sharpe: number | null;
  oos_return: number | null;
  oos_sharpe: number | null;
  oos_max_drawdown: number | null;
  hold_return: number | null;
  trades: number | null;
}

export interface HistogramBin {
  start: number;
  end: number;
  count: number;
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

export interface CriterionCheck {
  name: string;
  passed: boolean;
  value?: number;
  threshold?: number;
  reason?: string;
}

export interface LabReportDetail extends LabReportSummary {
  config: Record<string, unknown>;
  per_symbol: Record<string, unknown>;
  parameter_stability: Record<string, unknown>;
  montecarlo: Record<string, unknown>;
  checks: CriterionCheck[];
  exposure_by_risk_level: Record<string, number>;
  windows: LabWindow[];
  equity: { day: string; strategy: number; buy_and_hold: number | null }[];
  drawdown_histogram: DrawdownHistogram | null;
}

export type BacktestStatus = "queued" | "running" | "done" | "failed";

export interface BacktestJob {
  id: string;
  profile_id: string;
  status: BacktestStatus;
  config: Record<string, unknown>;
  report: Record<string, unknown> | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

export function listLabReports(token: string, fetcher?: Fetcher) {
  return apiFetch<LabReportSummary[]>("/lab/reports", token, {}, fetcher);
}

export function getLabReport(token: string, name: string, fetcher?: Fetcher) {
  return apiFetch<LabReportDetail>(`/lab/reports/${encodeURIComponent(name)}`, token, {}, fetcher);
}

export function listBacktests(token: string, fetcher?: Fetcher) {
  return apiFetch<BacktestJob[]>("/backtests", token, {}, fetcher);
}

// --- formulario para encolar un backtest -------------------------------------------------

export const SYMBOLS = ["BTCUSDT", "ETHUSDT"] as const;
export const TIMEFRAMES = ["15m", "30m", "4h", "1d"] as const;
export const MODES = [
  { value: "walk_forward", label: "Walk-forward" },
  { value: "in_sample", label: "In-sample" },
  { value: "out_of_sample", label: "Out-of-sample" },
] as const;
export const STRATEGIES = [
  { value: "trend", label: "Trend (Donchian)" },
  { value: "intraday_momentum", label: "Intraday momentum" },
  { value: "kronos", label: "Kronos" },
] as const;

export interface BacktestFormState {
  symbols: string[];
  timeframe: string;
  start: string;
  end: string;
  mode: string;
  strategies: string[];
  initialCapital: string;
}

export const EMPTY_BACKTEST_FORM: BacktestFormState = {
  symbols: ["BTCUSDT", "ETHUSDT"],
  timeframe: "1d",
  start: "2021-01-01",
  end: "2025-12-31",
  mode: "walk_forward",
  strategies: ["trend"],
  initialCapital: "",
};

const isoDate = z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "Usa el formato AAAA-MM-DD");

/** Mismas reglas que BacktestConfig en services/api/src/tm_api/schemas.py. */
export const backtestSchema = z
  .object({
    symbols: z.array(z.enum(SYMBOLS)).min(1, "Elige al menos un instrumento"),
    timeframe: z.enum(TIMEFRAMES),
    start: isoDate,
    end: isoDate,
    mode: z.enum(["walk_forward", "in_sample", "out_of_sample"]),
    strategies: z.array(z.string()),
    initialCapital: z
      .string()
      .trim()
      .refine((v) => v === "" || (Number.isFinite(Number(v)) && Number(v) > 0), "Debe ser mayor que 0"),
  })
  .refine((v) => v.end > v.start, { path: ["end"], message: "El fin debe ser posterior al inicio" });

export type BacktestFieldErrors = Partial<Record<keyof BacktestFormState, string>>;

export function validateBacktest(form: BacktestFormState):
  | { ok: true; body: { config: Record<string, unknown> } }
  | { ok: false; errors: BacktestFieldErrors } {
  const parsed = backtestSchema.safeParse(form);
  if (!parsed.success) {
    const errors: BacktestFieldErrors = {};
    for (const issue of parsed.error.issues) {
      const key = issue.path[0] as keyof BacktestFormState;
      if (key && !errors[key]) errors[key] = issue.message;
    }
    return { ok: false, errors };
  }
  const v = parsed.data;
  const config: Record<string, unknown> = {
    symbols: v.symbols,
    timeframe: v.timeframe,
    start: v.start,
    end: v.end,
    mode: v.mode,
    strategies: v.strategies,
  };
  if (v.initialCapital !== "") config.initial_capital = v.initialCapital;
  return { ok: true, body: { config } };
}

export function createBacktest(token: string, body: { config: Record<string, unknown> }, fetcher?: Fetcher) {
  return apiFetch<BacktestJob>(
    "/backtests",
    token,
    { method: "POST", body: JSON.stringify(body) },
    fetcher,
  );
}
