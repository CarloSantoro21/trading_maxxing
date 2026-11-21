/**
 * Ciclo de vida de las estrategias: transiciones permitidas, días en shadow sin fallas y
 * el registro atómico de una transición en D1.
 */

import { ApiError, conflict, fromDbError, notFound } from "./errors";
import { type Row, STRATEGY_COLS, nowIso, withJson } from "./db";
import type { StrategyStateT } from "./validation";

/** Días mínimos en shadow sin fallas para promover (igual que tm_agents.reference). */
export const MIN_SHADOW_DAYS = 7;
export const SYSTEM_ACTOR = "system";

export const ALLOWED_TRANSITIONS: ReadonlySet<string> = new Set([
  "research>lab",
  "research>retired",
  "lab>shadow",
  "lab>research",
  "lab>retired",
  "shadow>active",
  "shadow>lab",
  "shadow>retired",
  "active>probation",
  "active>retired",
  "probation>active",
  "probation>retired",
  "retired>lab",
]);

export const isAllowedTransition = (from: string, to: string): boolean => ALLOWED_TRANSITIONS.has(`${from}>${to}`);

/**
 * Días en shadow sin fallas; null si no está en shadow. Cuenta desde lo más reciente entre
 * la entrada al estado y live_metrics.shadow.clean_since (igual que public.shadow_days_clean).
 */
export function shadowDaysClean(strategy: Row, now: number = Date.now()): number | null {
  if (strategy.state !== "shadow") return null;
  const changed = Date.parse(String(strategy.state_changed_at ?? ""));
  if (Number.isNaN(changed)) return 0;
  const metrics = (strategy.live_metrics ?? {}) as Record<string, unknown>;
  const shadow = metrics.shadow as Record<string, unknown> | undefined;
  const clean = shadow && typeof shadow.clean_since === "string" ? Date.parse(shadow.clean_since) : Number.NaN;
  const since = Number.isNaN(clean) ? changed : Math.max(changed, clean);
  return Math.max(0, (now - since) / 86_400_000);
}

export function strategyOut(row: Row): Row {
  const out = withJson(row, ["params", "lab_report", "live_metrics"]);
  out.shadow_days_clean = shadowDaysClean(out);
  return out;
}

export async function getStrategy(db: D1Database, name: string): Promise<Row> {
  const row = await db.prepare(`SELECT ${STRATEGY_COLS} FROM strategies s WHERE s.name = ?`).bind(name).first<Row>();
  if (!row) throw notFound("Estrategia");
  return strategyOut(row);
}

export interface TransitionInput {
  strategy: Row;
  fromState: StrategyStateT;
  toState: StrategyStateT;
  actor: string;
  reason: string;
}

/**
 * Inserta la transición; los triggers de la migración 0002 validan que from_state siga
 * siendo el estado actual (si no, 409 stale_state) y actualizan strategies.state.
 */
export async function insertTransition(db: D1Database, t: TransitionInput): Promise<Row> {
  if (!isAllowedTransition(t.fromState, t.toState)) {
    throw new ApiError(409, `Transición no permitida: ${t.fromState} -> ${t.toState}`, "transition_not_allowed");
  }
  if (t.toState === "active" && t.actor === SYSTEM_ACTOR) {
    throw new ApiError(409, `La promoción ${t.fromState} -> active requiere un actor humano`, "transition_not_allowed");
  }
  try {
    const row = await db
      .prepare(
        `INSERT INTO strategy_transitions (strategy_id, from_state, to_state, actor, reason, created_at)
         VALUES (?, ?, ?, ?, ?, ?)
         RETURNING id, strategy_id, from_state, to_state, actor, reason, created_at`,
      )
      .bind(t.strategy.id as string, t.fromState, t.toState, t.actor, t.reason, nowIso())
      .first<Row>();
    return row as Row;
  } catch (err) {
    const mapped = fromDbError(err);
    if (mapped?.code === "stale_state") {
      throw conflict(`La estrategia ${String(t.strategy.name)} cambió de estado mientras se procesaba; reintenta`, "stale_state");
    }
    throw mapped ?? err;
  }
}
