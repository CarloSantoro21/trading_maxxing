/**
 * Pesos del PortfolioAllocator (port de tm_agents.allocator), solo lo que usa la
 * proyección de metas: fracción del capital por estrategia y razones de exclusión.
 *
 * La proyección no pasa matriz de correlación ni retornos al allocator, así que la
 * correlación es 0 y el peso base es `peso_por_horizonte / volatilidad`. Después se aplica
 * el estado (active completo, probation la mitad; lo liberado va a las activas) y el
 * nocional mínimo de 10 USDT por sleeve.
 */

export type Horizon = "short" | "medium" | "long";

export interface StrategyStats {
  name: string;
  state: string;
  volatility: number;
  oos_sharpe: number;
  horizon: Horizon | null;
  max_drawdown_p95: number | null;
}

export const HORIZON_WEIGHTS: Record<Horizon, Record<Horizon, number>> = {
  short: { short: 1.0, medium: 0.75, long: 0.5 },
  medium: { short: 0.75, medium: 1.0, long: 0.75 },
  long: { short: 0.5, medium: 0.75, long: 1.0 },
};

const STATE_MULTIPLIER: Record<string, number> = { active: 1.0, probation: 0.5 };
export const MIN_NOTIONAL = 10;

export interface Allocation {
  weights: Record<string, number>;
  excluded: Record<string, string>;
}

function horizonWeight(profile: Horizon, s: StrategyStats): number {
  if (!s.horizon) return 1;
  return HORIZON_WEIGHTS[profile]?.[s.horizon] ?? 1;
}

function exclusionReason(s: StrategyStats, horizon: Horizon, minSharpe = 0): string | null {
  if (!(s.state in STATE_MULTIPLIER)) return `estado ${s.state}: no opera con capital`;
  if (!Number.isFinite(s.volatility) || s.volatility <= 0) return "volatilidad validada no positiva o no finita";
  if (!Number.isFinite(s.oos_sharpe) || s.oos_sharpe <= minSharpe) {
    return `Sharpe out-of-sample de ${s.oos_sharpe.toFixed(2)} (se requiere mayor a ${minSharpe.toFixed(2)})`;
  }
  if (horizonWeight(horizon, s) <= 0) return `peso 0 para el horizonte ${horizon}`;
  return null;
}

function effectiveWeights(base: Map<string, number>, byName: Map<string, StrategyStats>): Record<string, number> {
  const names = [...base.keys()].sort();
  const total = names.reduce((acc, n) => acc + (base.get(n) ?? 0), 0);
  const shares: Record<string, number> = {};
  const weights: Record<string, number> = {};
  for (const n of names) {
    shares[n] = (base.get(n) ?? 0) / total;
    weights[n] = shares[n] * (STATE_MULTIPLIER[byName.get(n)!.state] ?? 0);
  }
  const freed = 1 - Object.values(weights).reduce((a, b) => a + b, 0);
  const active = names.filter((n) => byName.get(n)!.state === "active");
  const activeShare = active.reduce((acc, n) => acc + (shares[n] ?? 0), 0);
  if (freed > 0 && activeShare > 0) {
    for (const n of active) weights[n] = (weights[n] ?? 0) + (freed * (shares[n] ?? 0)) / activeShare;
  }
  return weights;
}

export function allocate(capital: number, horizon: Horizon, strategies: readonly StrategyStats[]): Allocation {
  const excluded: Record<string, string> = {};
  const base = new Map<string, number>();
  const byName = new Map(strategies.map((s) => [s.name, s]));
  for (const s of strategies) {
    const reason = exclusionReason(s, horizon);
    if (reason === null) base.set(s.name, horizonWeight(horizon, s) / s.volatility);
    else excluded[s.name] = reason;
  }
  let weights: Record<string, number> = {};
  while (base.size > 0) {
    weights = effectiveWeights(base, byName);
    const below = Object.entries(weights).filter(([, w]) => capital * w < MIN_NOTIONAL);
    if (below.length === 0) break;
    below.sort((a, b) => a[1] - b[1] || (a[0] < b[0] ? -1 : 1));
    const [smallest, w] = below[0]!;
    excluded[smallest] = `su sleeve (${(capital * w).toFixed(2)} USDT) no alcanza el nocional mínimo de ${MIN_NOTIONAL} USDT`;
    base.delete(smallest);
    weights = {};
  }
  return { weights, excluded };
}
