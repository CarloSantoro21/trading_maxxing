/**
 * Niveles de riesgo (igual que tm_agents.risk.RISK_TABLE y la función
 * private.risk_level_max_drawdown de Supabase): el drawdown máximo de cada nivel.
 */

export const MIN_CAPITAL = 500;

export const RISK_MAX_DRAWDOWN: Readonly<Record<number, number>> = Object.freeze({
  1: 0.1,
  2: 0.15,
  3: 0.2,
  4: 0.3,
  5: 0.4,
});

export function levelDrawdown(level: number): number {
  const dd = RISK_MAX_DRAWDOWN[level];
  if (dd === undefined) throw new Error("risk_level debe estar entre 1 y 5");
  return dd;
}
