import type { HistogramBin } from "./api";

/** Formato de números para las tablas y gráficas (es-MX). */

export function pct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${(value * 100).toFixed(digits)} %`;
}

export function num(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return value.toLocaleString("es-MX", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function usd(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${value.toLocaleString("es-MX", { maximumFractionDigits: 0 })} USDT`;
}

/** Submuestrea una serie larga a ~`max` puntos, conservando el primero y el último. */
export function downsample<T>(rows: T[], max = 300): T[] {
  if (rows.length <= max) return rows;
  const step = (rows.length - 1) / (max - 1);
  const out: T[] = [];
  for (let i = 0; i < max; i++) out.push(rows[Math.round(i * step)]);
  return out;
}

/** Equity como crecimiento de 1 USDT para comparar estrategia y comprar y mantener. */
export function normalizeEquity(rows: { day: string; strategy: number; buy_and_hold: number | null }[]) {
  const s0 = rows[0]?.strategy;
  const h0 = rows.find((r) => r.buy_and_hold !== null)?.buy_and_hold ?? null;
  return rows.map((r) => ({
    day: r.day,
    strategy: s0 ? r.strategy / s0 : null,
    buy_and_hold: h0 && r.buy_and_hold !== null ? r.buy_and_hold / h0 : null,
  }));
}

/** Barras del histograma con su punto medio en % y si quedan arriba del p95. */
export function histogramBars(bins: HistogramBin[], p95: number) {
  return bins.map((b) => ({
    mid: ((b.start + b.end) / 2) * 100,
    label: `${(b.start * 100).toFixed(1)}–${(b.end * 100).toFixed(1)} %`,
    count: b.count,
    tail: b.start >= p95,
  }));
}
