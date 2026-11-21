/**
 * Catálogo de estrategias (compartido) y acciones de admin sobre su ciclo de vida.
 * Mismo contrato que /strategies de FastAPI; cada estrategia trae además
 * `shadow_days_clean` (antes la calculaba PostgREST).
 *
 * También reemplaza las lecturas directas a Supabase de la página /strategies:
 * GET /strategies/overview devuelve estrategias, transiciones, equity de referencia e isAdmin.
 */

import { Hono } from "hono";
import { z } from "zod";
import { STRATEGY_COLS, type Row, all } from "../lib/db";
import { conflict } from "../lib/errors";
import { MIN_SHADOW_DAYS, getStrategy, insertTransition, shadowDaysClean, strategyOut } from "../lib/strategies";
import { type StrategyStateT, Uuid, body, intQuery, query } from "../lib/validation";
import { requireAdmin } from "../auth/middleware";
import type { AppEnv, CurrentUser } from "../types";

const TransitionRequest = z.strictObject({
  reason: z
    .string()
    .min(1)
    .max(1000)
    .transform((s) => s.trim())
    .refine((s) => s.length > 0, { message: "el motivo no puede estar vacío" }),
});

const TRANSITION_COLS = "id, strategy_id, from_state, to_state, actor, reason, created_at";
const REFERENCE_COLS = "strategy_id, started_at, day, state, equity, cash, positions_value, drawdown";

async function transition(db: D1Database, user: CurrentUser, name: string, to: StrategyStateT, reason: string) {
  const strategy = await getStrategy(db, name);
  const from = strategy.state as StrategyStateT;
  if (to === "active" && from !== "shadow" && from !== "probation") {
    throw conflict(`Solo se promueve desde shadow o probation; ${name} está en ${from}`);
  }
  if (to === "active" && from === "shadow") {
    const days = shadowDaysClean(strategy) ?? 0;
    if (days < MIN_SHADOW_DAYS) {
      throw conflict(`${name} lleva ${days.toFixed(1)} días en shadow sin fallas; se requieren al menos ${MIN_SHADOW_DAYS}`);
    }
  }
  if (to === "retired" && from === "retired") throw conflict(`${name} ya está retirada`);
  await insertTransition(db, { strategy, fromState: from, toState: to, actor: user.id, reason });
  return { strategy: await getStrategy(db, name), from_state: from, to_state: to, actor: user.id, reason };
}

export const strategies = new Hono<AppEnv>();

strategies.get("/", async (c) => {
  const rows = await all<Row>(c.env.DB.prepare(`SELECT ${STRATEGY_COLS} FROM strategies s ORDER BY s.name`));
  return c.json(rows.map(strategyOut));
});

/** Todo lo que necesita la página /strategies en una sola llamada. */
strategies.get("/overview", async (c) => {
  const db = c.env.DB;
  const [rows, transitions, equity] = await db.batch<Row>([
    db.prepare(`SELECT ${STRATEGY_COLS} FROM strategies s ORDER BY s.name`),
    db.prepare(`SELECT ${TRANSITION_COLS} FROM strategy_transitions ORDER BY created_at DESC, id DESC LIMIT 500`),
    db.prepare(`SELECT ${REFERENCE_COLS} FROM reference_equity ORDER BY day LIMIT 10000`),
  ]);
  return c.json({
    strategies: (rows?.results ?? []).map(strategyOut),
    transitions: transitions?.results ?? [],
    equity: equity?.results ?? [],
    isAdmin: c.get("user").role === "admin",
  });
});

strategies.get("/reference-equity", async (c) => {
  const q = query(c, z.object({ strategy_id: Uuid.optional(), limit: intQuery(1, 10000, 10000) }));
  const stmt = q.strategy_id
    ? c.env.DB.prepare(`SELECT ${REFERENCE_COLS} FROM reference_equity WHERE strategy_id = ? ORDER BY day LIMIT ?`).bind(q.strategy_id, q.limit)
    : c.env.DB.prepare(`SELECT ${REFERENCE_COLS} FROM reference_equity ORDER BY day LIMIT ?`).bind(q.limit);
  return c.json(await all<Row>(stmt));
});

strategies.get("/:name", async (c) => c.json(await getStrategy(c.env.DB, c.req.param("name"))));

strategies.get("/:name/transitions", async (c) => {
  const strategy = await getStrategy(c.env.DB, c.req.param("name"));
  const rows = await all<Row>(
    c.env.DB.prepare(`SELECT ${TRANSITION_COLS} FROM strategy_transitions WHERE strategy_id = ? ORDER BY created_at, id`).bind(
      strategy.id as string,
    ),
  );
  return c.json(rows);
});

strategies.post("/:name/promote", requireAdmin, async (c) => {
  const input = await body(c, TransitionRequest);
  return c.json(await transition(c.env.DB, c.get("user"), c.req.param("name"), "active", input.reason));
});

strategies.post("/:name/retire", requireAdmin, async (c) => {
  const input = await body(c, TransitionRequest);
  return c.json(await transition(c.env.DB, c.get("user"), c.req.param("name"), "retired", input.reason));
});
