/**
 * Block bootstrap circular (port de tm_agents.montecarlo.bootstrap y bootstrap_paths).
 *
 * Diferencias con la versión de numpy:
 *   * El generador es mulberry32 con semilla fija (no el PCG64 de numpy), así que las
 *     muestras no son idénticas a las de Python, aunque sí reproducibles: misma semilla,
 *     mismo resultado.
 *   * No guarda la matriz de trayectorias: recorre cada simulación una vez y se queda con
 *     lo necesario (drawdown máximo, retorno final, pico y el equity en los días del
 *     abanico). Así la memoria es O(simulaciones * puntos del abanico) y cabe en un Worker.
 */

/** PRNG mulberry32: rápido, determinista y suficiente para remuestrear. */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export interface BootstrapOptions {
  nSimulations: number;
  seed: number;
  blockSize: number;
  /** Pasos de cada trayectoria (por omisión, el número de retornos). */
  horizon?: number;
  initialCapital?: number;
  /** Pasos (0..horizon) en los que se guarda el equity para el abanico. */
  fanIndices?: readonly number[];
}

export interface BootstrapResult {
  maxDrawdowns: Float64Array;
  finalReturns: Float64Array;
  /** Máximo del equity / capital inicial (incluye el punto inicial, así que es >= 1). */
  peakGrowth: Float64Array;
  /** fan[k][s] = equity de la simulación s en el paso fanIndices[k]. */
  fan: Float64Array[];
}

export function bootstrap(returns: ArrayLike<number>, opts: BootstrapOptions): BootstrapResult {
  const n = returns.length;
  if (n === 0) throw new Error("se necesita al menos un retorno");
  const { nSimulations, seed } = opts;
  const block = Math.max(1, Math.min(opts.blockSize, n));
  const steps = opts.horizon ?? n;
  const initial = opts.initialCapital ?? 1;
  const fanIdx = opts.fanIndices ?? [];
  const rng = mulberry32(seed);

  const maxDrawdowns = new Float64Array(nSimulations);
  const finalReturns = new Float64Array(nSimulations);
  const peakGrowth = new Float64Array(nSimulations);
  const fan = fanIdx.map(() => new Float64Array(nSimulations));

  for (let s = 0; s < nSimulations; s++) {
    let equity = initial;
    let peak = initial;
    let maxDd = 0;
    let k = 0;
    while (k < fanIdx.length && fanIdx[k] === 0) fan[k++]![s] = equity;
    let step = 0;
    while (step < steps) {
      const start = Math.floor(rng() * n);
      for (let o = 0; o < block && step < steps; o++) {
        equity *= 1 + (returns[(start + o) % n] as number);
        step++;
        if (equity > peak) peak = equity;
        const dd = 1 - equity / peak;
        if (dd > maxDd) maxDd = dd;
        while (k < fanIdx.length && fanIdx[k] === step) fan[k++]![s] = equity;
      }
    }
    maxDrawdowns[s] = maxDd;
    finalReturns[s] = equity / initial - 1;
    peakGrowth[s] = peak / initial;
  }
  return { maxDrawdowns, finalReturns, peakGrowth, fan };
}

/** Copia ordenada de menor a mayor. */
export function sorted(values: ArrayLike<number>): Float64Array {
  return Float64Array.from(values).sort();
}

/** Percentil con interpolación lineal (el método por omisión de numpy). */
export function percentileSorted(values: Float64Array, p: number): number {
  const n = values.length;
  if (n === 0) return Number.NaN;
  const idx = (p / 100) * (n - 1);
  const lo = Math.floor(idx);
  const hi = Math.ceil(idx);
  const a = values[lo] as number;
  const b = values[hi] as number;
  return a + (b - a) * (idx - lo);
}

export function percentile(values: ArrayLike<number>, p: number): number {
  return percentileSorted(sorted(values), p);
}

export function mean(values: ArrayLike<number>): number {
  let total = 0;
  for (let i = 0; i < values.length; i++) total += values[i] as number;
  return values.length ? total / values.length : Number.NaN;
}

/** Desviación estándar muestral (ddof = 1). */
export function stdSample(values: ArrayLike<number>): number {
  const n = values.length;
  if (n < 2) return 0;
  const m = mean(values);
  let acc = 0;
  for (let i = 0; i < n; i++) acc += ((values[i] as number) - m) ** 2;
  return Math.sqrt(acc / (n - 1));
}

export interface HistogramBin {
  start: number;
  end: number;
  count: number;
}

/** Histograma con `bins` intervalos iguales en [lo, hi] (el último incluye hi, como numpy). */
export function histogram(values: ArrayLike<number>, bins: number, lo: number, hi: number): HistogramBin[] {
  const top = hi > lo ? hi : lo + 1;
  const width = (top - lo) / bins;
  const counts = new Array<number>(bins).fill(0);
  for (let i = 0; i < values.length; i++) {
    const v = values[i] as number;
    if (v < lo || v > top) continue;
    const idx = Math.min(bins - 1, Math.floor((v - lo) / width));
    counts[idx] = (counts[idx] ?? 0) + 1;
  }
  return counts.map((count, i) => ({ start: lo + i * width, end: i === bins - 1 ? top : lo + (i + 1) * width, count }));
}
