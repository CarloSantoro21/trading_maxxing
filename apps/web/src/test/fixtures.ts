import type { LabReportDetail, LabReportSummary } from "@/lib/backtests/api";
import type { GoalProbability } from "@/lib/goals/probability";

export const SUMMARY: LabReportSummary = {
  name: "trend",
  strategy: "trend",
  symbols: ["BTCUSDT", "ETHUSDT"],
  oos_start: "2022-01-01",
  oos_end: "2025-12-31",
  aggregate: { total_return: 0.42, sharpe: 0.9, max_drawdown: -0.21, trades: 30 },
  buy_and_hold: { total_return: 0.3, sharpe: 0.5, max_drawdown: -0.6 },
  max_drawdown_p95: 0.28,
  approved: true,
  reasons: [],
};

export const DETAIL: LabReportDetail = {
  ...SUMMARY,
  config: {},
  per_symbol: {},
  parameter_stability: {},
  montecarlo: {},
  checks: [
    { name: "sharpe", passed: true, reason: "Sharpe OOS 0.90 ≥ 0.5" },
    { name: "dd", passed: false, reason: "Drawdown p95 28 % > 25 %" },
  ],
  exposure_by_risk_level: { "1": 0.2, "5": 1 },
  windows: [
    {
      label: "W1",
      is_start: "2020-01-01",
      is_end: "2021-12-31",
      oos_start: "2022-01-01",
      oos_end: "2022-06-30",
      params_label: "n=20",
      is_sharpe: 1.1,
      oos_return: -0.05,
      oos_sharpe: -0.2,
      oos_max_drawdown: -0.1,
      hold_return: -0.4,
      trades: 4,
    },
  ],
  equity: [
    { day: "2022-01-01", strategy: 1000, buy_and_hold: 1000 },
    { day: "2022-01-02", strategy: 1010, buy_and_hold: 990 },
    { day: "2022-01-03", strategy: 1420, buy_and_hold: null },
  ],
  drawdown_histogram: {
    bins: [
      { start: 0, end: 0.1, count: 10 },
      { start: 0.1, end: 0.2, count: 50 },
      { start: 0.3, end: 0.4, count: 5 },
    ],
    p95: 0.28,
    p95_recomputed: 0.28,
    p50: 0.15,
    n_simulations: 1000,
    block_size: 20,
    seed: 7,
    horizon_days: 365,
    limit: 0.25,
  },
};

export const PROJECTION: GoalProbability = {
  goal_id: "g1",
  profile_id: "p1",
  target_return: 0.2,
  deadline: "2027-06-30",
  as_of: "2026-11-08",
  days: 234,
  capital: 1000,
  start_equity: 1000,
  target_equity: 1200,
  probability: 0.35,
  probability_at_deadline: 0.3,
  invested_fraction: 0.6,
  final_equity_p10: 900,
  final_equity_p50: 1080,
  final_equity_p90: 1300,
  n_simulations: 2000,
  block_size: 20,
  seed: 42,
  fan: [
    { day: "2026-11-08", p10: 1000, p50: 1000, p90: 1000 },
    { day: "2027-06-30", p10: 900, p50: 1080, p90: 1300 },
  ],
  strategies: [
    {
      name: "trend",
      state: "live",
      source: "reference",
      report_source: "file",
      observations: 500,
      weight: 1,
      exposure: 0.6,
      excluded: null,
    },
  ],
  notes: ["Sin historial suficiente para kronos."],
};
