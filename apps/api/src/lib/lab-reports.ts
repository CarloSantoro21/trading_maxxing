/**
 * Reportes del laboratorio empaquetados en el Worker (los genera scripts/build-lab-reports.ts).
 *
 * Son de la app, no de un usuario: cualquier usuario autenticado los lee.
 */

import bundleJson from "../generated/lab-reports.json";
import { type LabBundle, type LabDetail, type LabSummary, REPORT_NAME } from "./lab-core";

const bundle = bundleJson as unknown as LabBundle;

export function listNames(): string[] {
  return Object.keys(bundle.reports).sort();
}

const SUMMARY_KEYS: (keyof LabSummary)[] = [
  "name",
  "strategy",
  "symbols",
  "oos_start",
  "oos_end",
  "aggregate",
  "buy_and_hold",
  "max_drawdown_p95",
  "approved",
  "reasons",
];

export function listSummaries(): LabSummary[] {
  return listNames().map((name) => {
    const d = bundle.reports[name]!.detail;
    return Object.fromEntries(SUMMARY_KEYS.map((k) => [k, d[k]])) as unknown as LabSummary;
  });
}

export function getDetail(name: string): LabDetail | null {
  if (!REPORT_NAME.test(name)) return null;
  return bundle.reports[name]?.detail ?? null;
}

/**
 * Reporte completo para la proyección (equity_daily.strategy, aggregate, montecarlo...),
 * o null si no hay archivo del laboratorio con ese nombre.
 */
export function getProjectionReport(name: string): Record<string, unknown> | null {
  if (!REPORT_NAME.test(name)) return null;
  const entry = bundle.reports[name];
  if (!entry) return null;
  return {
    ...entry.projection,
    equity_daily: { strategy: entry.detail.equity.map((p) => [p.day, p.strategy]) },
  };
}
