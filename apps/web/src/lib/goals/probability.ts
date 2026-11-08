import { apiFetch, type Fetcher } from "@/lib/tmApi";

/** GET /goals y GET /goals/{id}/probability (Monte Carlo #3, #38). */

export interface GoalRow {
  id: string;
  profile_id: string;
  target_return: number;
  deadline: string;
}

export interface FanPoint {
  day: string;
  p10: number;
  p50: number;
  p90: number;
}

export interface StrategyContribution {
  name: string;
  state: string;
  source: "reference" | "lab_report" | "none";
  report_source: "db" | "file" | "none";
  observations: number;
  weight: number;
  exposure: number;
  excluded: string | null;
}

export interface GoalProbability {
  goal_id: string;
  profile_id: string;
  target_return: number;
  deadline: string;
  as_of: string;
  days: number;
  capital: number;
  start_equity: number;
  target_equity: number;
  probability: number;
  probability_at_deadline: number;
  invested_fraction: number;
  final_equity_p10: number;
  final_equity_p50: number;
  final_equity_p90: number;
  n_simulations: number;
  block_size: number;
  seed: number;
  fan: FanPoint[];
  strategies: StrategyContribution[];
  notes: string[];
}

export function listGoals(token: string, fetcher?: Fetcher) {
  return apiFetch<GoalRow[]>("/goals", token, {}, fetcher);
}

export function getGoalProbability(token: string, goalId: string, fetcher?: Fetcher) {
  return apiFetch<GoalProbability>(`/goals/${encodeURIComponent(goalId)}/probability`, token, {}, fetcher);
}

/** Datos del abanico para Recharts: banda p10–p90 como rango y la mediana como línea. */
export function fanSeries(fan: FanPoint[]) {
  return fan.map((p) => ({ day: p.day, band: [p.p10, p.p90] as [number, number], p10: p.p10, p50: p.p50, p90: p.p90 }));
}

/** Lectura en palabras de la probabilidad (sin prometer nada). */
export function probabilityLabel(p: number): string {
  if (p >= 0.8) return "Muy probable";
  if (p >= 0.6) return "Probable";
  if (p >= 0.4) return "Incierta";
  if (p >= 0.2) return "Poco probable";
  return "Muy poco probable";
}

export const SOURCE_LABEL: Record<StrategyContribution["source"], string> = {
  reference: "Portafolio de referencia",
  lab_report: "Out-of-sample del laboratorio",
  none: "Sin historial",
};
