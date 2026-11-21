/**
 * Exposición de cada sleeve según el nivel de riesgo (port de
 * tm_agents.orchestrator.exposure). Ver el docstring de Python para la justificación.
 */

import { levelDrawdown } from "./risk";

export type Curve = readonly (readonly [number, number])[];

export const CONSERVATIVE_DD_FACTOR = 1.3;

export const TREND_SENSITIVITY: Curve = [
  [1.0, 0.4383],
  [0.9, 0.4023],
  [0.8, 0.365],
  [0.7, 0.3257],
  [0.6, 0.2847],
  [0.5, 0.2418],
  [0.4, 0.1975],
  [0.3, 0.151],
  [0.2, 0.1025],
  [0.1, 0.0522],
];

export const TREND_VOL_SENSITIVITY: Curve = [
  [1.0, 0.3561],
  [0.9, 0.3254],
  [0.8, 0.2938],
  [0.7, 0.2609],
  [0.6, 0.2272],
  [0.5, 0.1923],
  [0.4, 0.1563],
  [0.3, 0.1191],
  [0.2, 0.0807],
  [0.1, 0.041],
];

export const KNOWN_SENSITIVITY: Readonly<Record<string, Curve>> = {
  trend: TREND_SENSITIVITY,
  trend_vol: TREND_VOL_SENSITIVITY,
};

export function sensitivityFor(name: string, reported: Curve | null | undefined): Curve | null {
  if (reported && reported.length > 0) return reported;
  return KNOWN_SENSITIVITY[name] ?? null;
}

function relativeCurve(points: Curve): [number, number][] | null {
  const clean = points
    .map(([k, d]) => [Number(k), Number(d)] as [number, number])
    .filter(([k, d]) => Number.isFinite(k) && Number.isFinite(d) && k > 0 && k <= 1 && d > 0)
    .sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const full = clean.find(([k]) => k === 1);
  if (!full) return null;
  const base = full[1];
  const curve: [number, number][] = [[0, 0], ...clean.map(([k, d]) => [k, d / base] as [number, number])];
  for (let i = 1; i < curve.length; i++) {
    if (curve[i]![1] < curve[i - 1]![1]) return null;
  }
  return curve;
}

function invert(curve: [number, number][], ratio: number): number {
  for (let i = 1; i < curve.length; i++) {
    const [k0, r0] = curve[i - 1]!;
    const [k1, r1] = curve[i]!;
    if (r0 <= ratio && ratio <= r1) {
      if (r1 === r0) return k0;
      return k0 + ((ratio - r0) * (k1 - k0)) / (r1 - r0);
    }
  }
  return 1;
}

/** Fracción del sleeve que invierte un bot de `riskLevel` (0 a 1). */
export function exposureForLevel(riskLevel: number, p95: number | null | undefined, sensitivity?: Curve | null): number {
  const target = levelDrawdown(riskLevel);
  if (p95 == null || !Number.isFinite(p95) || p95 <= 0) return 0;
  const ratio = target / p95;
  if (ratio >= 1) return 1;
  const curve = sensitivity ? relativeCurve(sensitivity) : null;
  if (!curve) return Math.max(0, Math.min(1, ratio / CONSERVATIVE_DD_FACTOR));
  return invert(curve, ratio);
}
