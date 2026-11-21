/**
 * Ayudas para D1: ids, fechas, columnas JSON y consultas de pertenencia.
 *
 * En Supabase, RLS decidía qué filas veía cada usuario. En D1 no hay RLS: cada consulta
 * de la API pública filtra por el `user_id` del usuario autenticado (directo en profiles o
 * con JOIN profiles en goals, bots, trades, equity_snapshots y backtests). Las funciones
 * `owned*` de este módulo son ese filtro.
 */

import { ApiError, notFound } from "./errors";

export type Row = Record<string, unknown>;

export const newId = (): string => crypto.randomUUID();
export const nowIso = (): string => new Date().toISOString();

/** Convierte las columnas JSON (TEXT) de una fila en objetos. */
export function withJson<T extends Row>(row: T, fields: readonly string[]): T {
  const out: Row = { ...row };
  for (const field of fields) {
    const value = out[field];
    if (typeof value === "string") {
      try {
        out[field] = JSON.parse(value) as unknown;
      } catch {
        out[field] = null;
      }
    }
  }
  return out as T;
}

export const toJson = (value: unknown): string => JSON.stringify(value ?? {});

export const placeholders = (n: number): string => Array.from({ length: n }, () => "?").join(", ");

// --- columnas y mapeo por tabla ------------------------------------------------------

export const PROFILE_COLS =
  "p.id, p.user_id, p.horizon, p.risk_level, p.capital, p.max_drawdown, p.instruments, p.created_at, p.updated_at";
export const GOAL_COLS = "g.id, g.profile_id, g.target_return, g.deadline, g.created_at, g.updated_at";
export const BOT_COLS =
  "b.id, b.profile_id, b.name, b.status, b.strategy_config, b.started_at, b.stopped_at, b.created_at, b.updated_at";
export const TRADE_COLS =
  "t.id, t.bot_id, t.symbol, t.side, t.quantity, t.entry_price, t.exit_price, t.stop_loss, t.take_profit, t.fees, t.pnl, t.rationale, t.opened_at, t.closed_at";
export const BACKTEST_COLS =
  "bt.id, bt.profile_id, bt.status, bt.config, bt.report, bt.error, bt.started_at, bt.finished_at, bt.created_at, bt.updated_at";
export const STRATEGY_COLS =
  "s.id, s.name, s.state, s.params, s.lab_report, s.live_metrics, s.state_changed_at, s.created_at, s.updated_at";

export const profileOut = (r: Row): Row => withJson(r, ["instruments"]);
export const botOut = (r: Row): Row => withJson(r, ["strategy_config"]);
export const tradeOut = (r: Row): Row => withJson(r, ["rationale"]);
export const backtestOut = (r: Row): Row => withJson(r, ["config", "report"]);

export async function all<T = Row>(stmt: D1PreparedStatement): Promise<T[]> {
  const res = await stmt.all<T>();
  return res.results ?? [];
}

// --- pertenencia (lo que antes hacía RLS) ---------------------------------------------

/** El perfil indicado (si es del usuario) o, sin id, el único perfil del usuario. */
export async function resolveProfile(db: D1Database, userId: string, profileId?: string | null): Promise<Row> {
  const row = profileId
    ? await db
        .prepare(`SELECT ${PROFILE_COLS} FROM profiles p WHERE p.id = ? AND p.user_id = ?`)
        .bind(profileId, userId)
        .first<Row>()
    : await db.prepare(`SELECT ${PROFILE_COLS} FROM profiles p WHERE p.user_id = ?`).bind(userId).first<Row>();
  if (!row) {
    if (profileId) throw notFound("Perfil");
    throw new ApiError(404, "Aún no tienes perfil de inversión; créalo con POST /profiles");
  }
  return profileOut(row);
}

export async function ownedGoal(db: D1Database, userId: string, goalId: string): Promise<Row> {
  const row = await db
    .prepare(`SELECT ${GOAL_COLS} FROM goals g JOIN profiles p ON p.id = g.profile_id WHERE g.id = ? AND p.user_id = ?`)
    .bind(goalId, userId)
    .first<Row>();
  if (!row) throw notFound("Meta");
  return row;
}

export async function ownedBot(db: D1Database, userId: string, botId: string): Promise<Row> {
  const row = await db
    .prepare(`SELECT ${BOT_COLS} FROM bots b JOIN profiles p ON p.id = b.profile_id WHERE b.id = ? AND p.user_id = ?`)
    .bind(botId, userId)
    .first<Row>();
  if (!row) throw notFound("Bot");
  return botOut(row);
}

/** Ids de los bots del usuario (de su perfil). */
export async function userBotIds(db: D1Database, userId: string): Promise<string[]> {
  const rows = await all<{ id: string }>(
    db.prepare("SELECT b.id FROM bots b JOIN profiles p ON p.id = b.profile_id WHERE p.user_id = ?").bind(userId),
  );
  return rows.map((r) => r.id);
}

export const num = (value: unknown): number => {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : 0;
};
