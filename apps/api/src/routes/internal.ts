/**
 * Endpoints internos para el motor que corre en la Mac (agents, engine, forecast).
 *
 * Reemplazan lo que el motor hacía por PostgREST con la llave service_role y lo que pasaba
 * por los streams de Redis que escribía la API (tm:commands, tm:backtests). Todos exigen
 * X-Service-Token (o un service token de Access) y saltan el filtro por usuario.
 *
 * Errores con `code` para el WorkerApiBackend de Python:
 *   strategy_exists (409), stale_state (409), transition_not_allowed (409),
 *   not_found (404), check_violation / foreign_key_violation (422).
 */

import { Hono } from "hono";
import { z } from "zod";
import { BACKTEST_COLS, STRATEGY_COLS, type Row, all, backtestOut, botOut, newId, nowIso, toJson, tradeOut, withJson } from "../lib/db";
import { ApiError, conflict, fromDbError } from "../lib/errors";
import { getStrategy, insertTransition, strategyOut } from "../lib/strategies";
import {
  BacktestStatus,
  BotStatus,
  IsoDate,
  IsoDateTime,
  JsonObject,
  Num,
  StrategyState,
  Symbol,
  Uuid,
  body,
  intQuery,
  pathUuid,
  query,
} from "../lib/validation";
import { readSettings } from "./users";
import type { AppEnv } from "../types";

export const internal = new Hono<AppEnv>();

const nf = (what: string) => new ApiError(404, `${what} no encontrado`, "not_found");

async function guarded<T>(fn: () => Promise<T>): Promise<T> {
  try {
    return await fn();
  } catch (err) {
    if (err instanceof ApiError) throw err;
    throw fromDbError(err) ?? err;
  }
}

const StrategyName = z.string().regex(/^[a-z][a-z0-9_]{0,63}$/, "nombre inválido (minúsculas, dígitos y _)");
const csvStates = z
  .string()
  .optional()
  .transform((s) => (s ? s.split(",").map((x) => x.trim()).filter(Boolean) : []))
  .pipe(z.array(StrategyState));

// --- registro de estrategias (tm_agents.registry) ----------------------------------------

internal.get("/strategies", async (c) => {
  const q = query(c, z.object({ state: csvStates }));
  const where = q.state.length ? `WHERE s.state IN (${q.state.map(() => "?").join(", ")})` : "";
  const rows = await all<Row>(c.env.DB.prepare(`SELECT ${STRATEGY_COLS} FROM strategies s ${where} ORDER BY s.name`).bind(...q.state));
  return c.json(rows.map(strategyOut));
});

internal.get("/strategies/:name", async (c) => {
  const row = await c.env.DB.prepare(`SELECT ${STRATEGY_COLS} FROM strategies s WHERE s.name = ?`).bind(c.req.param("name")).first<Row>();
  if (!row) throw nf("Estrategia");
  return c.json(strategyOut(row));
});

internal.post("/strategies", async (c) => {
  const input = await body(c, z.strictObject({ name: StrategyName, params: JsonObject.default({}) }));
  const now = nowIso();
  try {
    const row = await c.env.DB.prepare(
      `INSERT INTO strategies (id, name, state, params, state_changed_at, created_at, updated_at)
       VALUES (?, ?, 'research', ?, ?, ?, ?) RETURNING *`,
    )
      .bind(newId(), input.name, toJson(input.params), now, now, now)
      .first<Row>();
    return c.json(strategyOut(row as Row), 201);
  } catch (err) {
    const mapped = fromDbError(err);
    if (mapped?.code === "unique_violation") throw conflict(`ya existe la estrategia '${input.name}'`, "strategy_exists");
    throw mapped ?? err;
  }
});

internal.patch("/strategies/:name", async (c) => {
  const input = await body(
    c,
    z
      .strictObject({ params: JsonObject.optional(), lab_report: JsonObject.nullable().optional(), live_metrics: JsonObject.optional() })
      .refine((v) => Object.keys(v).length > 0, { message: "envía al menos un campo" }),
  );
  const sets: string[] = [];
  const values: (string | null)[] = [];
  for (const key of ["params", "lab_report", "live_metrics"] as const) {
    if (input[key] !== undefined) {
      sets.push(`${key} = ?`);
      values.push(input[key] === null ? null : toJson(input[key]));
    }
  }
  const row = await guarded(() =>
    c.env.DB.prepare(`UPDATE strategies SET ${sets.join(", ")}, updated_at = ? WHERE name = ? RETURNING *`)
      .bind(...values, nowIso(), c.req.param("name"))
      .first<Row>(),
  );
  if (!row) throw nf("Estrategia");
  return c.json(strategyOut(row));
});

internal.get("/strategies/:name/transitions", async (c) => {
  const strategy = await getStrategy(c.env.DB, c.req.param("name")).catch(() => {
    throw nf("Estrategia");
  });
  const rows = await all<Row>(
    c.env.DB.prepare(
      "SELECT id, strategy_id, from_state, to_state, actor, reason, created_at FROM strategy_transitions WHERE strategy_id = ? ORDER BY created_at, id",
    ).bind(strategy.id as string),
  );
  return c.json(rows.map((r) => ({ ...r, strategy: strategy.name })));
});

internal.post("/strategies/:name/transitions", async (c) => {
  const input = await body(
    c,
    z.strictObject({
      from_state: StrategyState,
      to_state: StrategyState,
      actor: z.union([z.literal("system"), Uuid]),
      reason: z.string().trim().min(1).max(1000),
    }),
  );
  const strategy = await getStrategy(c.env.DB, c.req.param("name")).catch(() => {
    throw nf("Estrategia");
  });
  if (strategy.state !== input.from_state) {
    throw conflict(`'${String(strategy.name)}' está en ${String(strategy.state)}, no en ${input.from_state}`, "stale_state");
  }
  const row = await insertTransition(c.env.DB, {
    strategy,
    fromState: input.from_state,
    toState: input.to_state,
    actor: input.actor,
    reason: input.reason,
  });
  return c.json({ ...row, strategy: strategy.name }, 201);
});

// --- bots y perfiles (BotSupervisor) -----------------------------------------------------

const BOT_WITH_PROFILE = `SELECT b.id, b.name, b.status, b.strategy_config, b.profile_id,
  p.risk_level, p.capital, p.max_drawdown, p.horizon, p.instruments, p.user_id
  FROM bots b JOIN profiles p ON p.id = b.profile_id`;

function botWithProfile(r: Row): Row {
  const bot = botOut(r);
  return {
    id: bot.id,
    name: bot.name,
    status: bot.status,
    strategy_config: bot.strategy_config,
    profile_id: bot.profile_id,
    user_id: bot.user_id,
    profile: withJson(
      { risk_level: r.risk_level, capital: r.capital, max_drawdown: r.max_drawdown, horizon: r.horizon, instruments: r.instruments },
      ["instruments"],
    ),
  };
}

internal.get("/bots", async (c) => {
  const q = query(c, z.object({ status: BotStatus.optional() }));
  const stmt = q.status
    ? c.env.DB.prepare(`${BOT_WITH_PROFILE} WHERE b.status = ? ORDER BY b.created_at`).bind(q.status)
    : c.env.DB.prepare(`${BOT_WITH_PROFILE} ORDER BY b.created_at`);
  return c.json((await all<Row>(stmt)).map(botWithProfile));
});

internal.get("/bots/:id", async (c) => {
  const row = await c.env.DB.prepare(`${BOT_WITH_PROFILE} WHERE b.id = ?`).bind(pathUuid(c, "id")).first<Row>();
  if (!row) throw nf("Bot");
  return c.json(botWithProfile(row));
});

// --- trades (TradeStateRepository y SupabaseTradeRecorder) --------------------------------

const Positive = Num.pipe(z.number().gt(0));
const NonNegative = Num.pipe(z.number().min(0));

internal.get("/trades", async (c) => {
  const q = query(
    c,
    z.object({
      bot_id: Uuid.optional(),
      open: z.enum(["true", "false"]).optional(),
      side: z.enum(["buy", "sell"]).optional(),
      limit: intQuery(1, 1000, 1000),
      offset: intQuery(0, Number.MAX_SAFE_INTEGER, 0),
    }),
  );
  const where: string[] = [];
  const values: (string | number)[] = [];
  if (q.bot_id) {
    where.push("bot_id = ?");
    values.push(q.bot_id);
  }
  if (q.open === "true") where.push("closed_at IS NULL");
  if (q.open === "false") where.push("closed_at IS NOT NULL");
  if (q.side) {
    where.push("side = ?");
    values.push(q.side);
  }
  const rows = await all<Row>(
    c.env.DB.prepare(
      `SELECT id, bot_id, symbol, side, quantity, entry_price, exit_price, stop_loss, take_profit, fees, pnl, rationale, opened_at, closed_at
       FROM trades ${where.length ? `WHERE ${where.join(" AND ")}` : ""} ORDER BY opened_at, id LIMIT ? OFFSET ?`,
    ).bind(...values, q.limit, q.offset),
  );
  return c.json(rows.map(tradeOut));
});

const TradeCreate = z.strictObject({
  bot_id: Uuid,
  symbol: Symbol,
  side: z.enum(["buy", "sell"]),
  quantity: Positive,
  entry_price: Positive,
  stop_loss: Positive.nullable().optional(),
  take_profit: Positive.nullable().optional(),
  fees: NonNegative.default(0),
  rationale: JsonObject.default({}),
  opened_at: IsoDateTime.optional(),
});

internal.post("/trades", async (c) => {
  const t = await body(c, TradeCreate);
  const now = nowIso();
  const row = await guarded(() =>
    c.env.DB.prepare(
      `INSERT INTO trades (id, bot_id, symbol, side, quantity, entry_price, stop_loss, take_profit, fees, rationale, opened_at, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING *`,
    )
      .bind(
        newId(),
        t.bot_id,
        t.symbol,
        t.side,
        t.quantity,
        t.entry_price,
        t.stop_loss ?? null,
        t.take_profit ?? null,
        t.fees,
        toJson(t.rationale),
        t.opened_at ?? now,
        now,
        now,
      )
      .first<Row>(),
  );
  return c.json(tradeOut(row as Row), 201);
});

const TradePatch = z
  .strictObject({
    quantity: Positive.optional(),
    entry_price: Positive.optional(),
    fees: NonNegative.optional(),
    rationale: JsonObject.optional(),
    exit_price: Positive.nullable().optional(),
    closed_at: IsoDateTime.nullable().optional(),
    pnl: Num.nullable().optional(),
    stop_loss: Positive.nullable().optional(),
    take_profit: Positive.nullable().optional(),
  })
  .refine((v) => Object.keys(v).length > 0, { message: "envía al menos un campo" });

internal.patch("/trades/:id", async (c) => {
  const id = pathUuid(c, "id");
  const input = await body(c, TradePatch);
  const sets: string[] = [];
  const values: (string | number | null)[] = [];
  for (const [key, value] of Object.entries(input)) {
    if (value === undefined) continue;
    sets.push(`${key} = ?`);
    values.push(key === "rationale" ? toJson(value) : (value as string | number | null));
  }
  const row = await guarded(() =>
    c.env.DB.prepare(`UPDATE trades SET ${sets.join(", ")}, updated_at = ? WHERE id = ? RETURNING *`).bind(...values, nowIso(), id).first<Row>(),
  );
  if (!row) throw nf("Trade");
  return c.json(tradeOut(row));
});

// --- equity de los bots ------------------------------------------------------------------

const Snapshot = z.strictObject({
  bot_id: Uuid,
  ts: IsoDateTime,
  equity: NonNegative,
  cash: NonNegative,
  positions_value: Num.default(0),
  drawdown: Num.pipe(z.number().min(0).max(1)).default(0),
});

internal.post("/equity-snapshots", async (c) => {
  const input = await body(c, z.strictObject({ snapshots: z.array(Snapshot).min(1).max(500) }));
  const now = nowIso();
  await guarded(() =>
    c.env.DB.batch(
      input.snapshots.map((s) =>
        c.env.DB.prepare(
          `INSERT INTO equity_snapshots (bot_id, ts, equity, cash, positions_value, drawdown, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (bot_id, ts) DO UPDATE SET equity = excluded.equity, cash = excluded.cash,
             positions_value = excluded.positions_value, drawdown = excluded.drawdown, updated_at = excluded.updated_at`,
        ).bind(s.bot_id, s.ts, s.equity, s.cash, s.positions_value, s.drawdown, now, now),
      ),
    ),
  );
  return c.json({ upserted: input.snapshots.length });
});

// --- ledger de referencia (tm_agents.reference.store) ------------------------------------

const TrackedState = z.enum(["shadow", "active", "probation"]);

const ReferenceTrade = z.strictObject({
  strategy_id: Uuid,
  started_at: IsoDateTime,
  state: TrackedState,
  symbol: Symbol,
  quantity: Positive,
  entry_price: Positive,
  exit_price: Positive,
  fees: NonNegative.default(0),
  pnl: Num,
  ret: Num.pipe(z.number().min(-1)),
  opened_at: IsoDateTime,
  closed_at: IsoDateTime,
});

internal.post("/reference/trades", async (c) => {
  const input = await body(c, z.strictObject({ rows: z.array(ReferenceTrade).max(500) }));
  if (input.rows.length === 0) return c.json({ received: 0, inserted: 0 });
  const results = await guarded(() =>
    c.env.DB.batch(
      input.rows.map((r) =>
        c.env.DB.prepare(
          `INSERT INTO reference_trades (strategy_id, started_at, state, symbol, quantity, entry_price, exit_price, fees, pnl, ret, opened_at, closed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (strategy_id, symbol, closed_at) DO NOTHING`,
        ).bind(r.strategy_id, r.started_at, r.state, r.symbol, r.quantity, r.entry_price, r.exit_price, r.fees, r.pnl, r.ret, r.opened_at, r.closed_at),
      ),
    ),
  );
  const inserted = results.reduce((acc, r) => acc + (r.meta.changes ?? 0), 0);
  return c.json({ received: input.rows.length, inserted });
});

const ReferencePoint = z.strictObject({
  strategy_id: Uuid,
  started_at: IsoDateTime,
  day: IsoDate,
  state: TrackedState,
  equity: NonNegative,
  cash: NonNegative,
  positions_value: NonNegative.default(0),
  drawdown: Num.pipe(z.number().min(0).max(1)).default(0),
});

internal.put("/reference/equity", async (c) => {
  const input = await body(c, z.strictObject({ points: z.array(ReferencePoint).max(500) }));
  if (input.points.length === 0) return c.json({ upserted: 0 });
  const now = nowIso();
  await guarded(() =>
    c.env.DB.batch(
      input.points.map((p) =>
        c.env.DB.prepare(
          `INSERT INTO reference_equity (strategy_id, started_at, day, state, equity, cash, positions_value, drawdown, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (strategy_id, day) DO UPDATE SET started_at = excluded.started_at, state = excluded.state,
             equity = excluded.equity, cash = excluded.cash, positions_value = excluded.positions_value,
             drawdown = excluded.drawdown, updated_at = excluded.updated_at`,
        ).bind(p.strategy_id, p.started_at, p.day, p.state, p.equity, p.cash, p.positions_value, p.drawdown, now, now),
      ),
    ),
  );
  return c.json({ upserted: input.points.length });
});

internal.get("/reference/equity", async (c) => {
  const q = query(c, z.object({ strategy_id: Uuid }));
  const rows = await all<Row>(
    c.env.DB.prepare(
      "SELECT strategy_id, started_at, day, state, equity, cash, positions_value, drawdown FROM reference_equity WHERE strategy_id = ? ORDER BY day",
    ).bind(q.strategy_id),
  );
  return c.json(rows);
});

// --- decisiones y estado del orquestador --------------------------------------------------

internal.post("/decisions", async (c) => {
  const Decision = z.strictObject({
    bot_id: Uuid.nullable().optional(),
    strategy: z.string().max(64).nullable().optional(),
    symbol: z.string().max(20).nullable().optional(),
    kind: z.string().min(1).max(64),
    payload: JsonObject.default({}),
    decided_at: IsoDateTime.optional(),
  });
  const input = await body(c, z.strictObject({ decisions: z.array(Decision).min(1).max(500) }));
  const now = nowIso();
  await guarded(() =>
    c.env.DB.batch(
      input.decisions.map((d) =>
        c.env.DB.prepare("INSERT INTO decisions (bot_id, strategy, symbol, kind, payload, decided_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)").bind(
          d.bot_id ?? null,
          d.strategy ?? null,
          d.symbol ?? null,
          d.kind,
          toJson(d.payload),
          d.decided_at ?? now,
          now,
        ),
      ),
    ),
  );
  return c.json({ inserted: input.decisions.length }, 201);
});

const StateKey = z.string().min(1).max(128);

internal.get("/state/:key", async (c) => {
  const key = StateKey.parse(c.req.param("key"));
  const row = await c.env.DB.prepare("SELECT key, value, updated_at FROM orchestrator_state WHERE key = ?").bind(key).first<Row>();
  if (!row) throw nf("Estado");
  return c.json(withJson(row, ["value"]));
});

internal.put("/state/:key", async (c) => {
  const key = StateKey.parse(c.req.param("key"));
  const input = await body(c, z.strictObject({ value: z.unknown() }));
  const now = nowIso();
  await c.env.DB.prepare(
    `INSERT INTO orchestrator_state (key, value, updated_at) VALUES (?, ?, ?)
     ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at`,
  )
    .bind(key, JSON.stringify(input.value ?? null), now)
    .run();
  return c.json({ key, value: input.value ?? null, updated_at: now });
});

// --- comandos de bots (antes tm:commands) -------------------------------------------------

internal.get("/commands", async (c) => {
  const q = query(c, z.object({ after: intQuery(0, Number.MAX_SAFE_INTEGER, 0), limit: intQuery(1, 500, 100) }));
  const rows = await all<Row>(
    c.env.DB.prepare(
      `SELECT seq, id, type, bot_id, user_id, reason, payload, issued_at FROM commands
       WHERE acked_at IS NULL AND seq > ? ORDER BY seq LIMIT ?`,
    ).bind(q.after, q.limit),
  );
  return c.json(rows.map((r) => withJson(r, ["payload"])));
});

internal.post("/commands/:id/ack", async (c) => {
  const id = pathUuid(c, "id");
  const input = await body(
    c,
    z.strictObject({ status: z.enum(["done", "failed"]).default("done"), error: z.string().max(2000).nullable().optional() }).default({ status: "done" }),
  );
  const row = await c.env.DB.prepare(
    "UPDATE commands SET acked_at = ?, ack_status = ?, ack_error = ? WHERE id = ? RETURNING seq, id, type, bot_id, acked_at, ack_status, ack_error",
  )
    .bind(nowIso(), input.status, input.error ?? null, id)
    .first<Row>();
  if (!row) throw nf("Comando");
  return c.json(row);
});

// --- backtests (antes tm:backtests) -------------------------------------------------------

internal.get("/backtests", async (c) => {
  const q = query(c, z.object({ status: BacktestStatus.optional(), limit: intQuery(1, 100, 20) }));
  const stmt = q.status
    ? c.env.DB.prepare(
        `SELECT ${BACKTEST_COLS}, p.user_id FROM backtests bt JOIN profiles p ON p.id = bt.profile_id WHERE bt.status = ? ORDER BY bt.created_at LIMIT ?`,
      ).bind(q.status, q.limit)
    : c.env.DB.prepare(
        `SELECT ${BACKTEST_COLS}, p.user_id FROM backtests bt JOIN profiles p ON p.id = bt.profile_id ORDER BY bt.created_at LIMIT ?`,
      ).bind(q.limit);
  return c.json((await all<Row>(stmt)).map(backtestOut));
});

internal.patch("/backtests/:id", async (c) => {
  const id = pathUuid(c, "id");
  const input = await body(
    c,
    z
      .strictObject({
        status: BacktestStatus.optional(),
        report: JsonObject.nullable().optional(),
        error: z.string().max(5000).nullable().optional(),
        started_at: IsoDateTime.nullable().optional(),
        finished_at: IsoDateTime.nullable().optional(),
      })
      .refine((v) => Object.keys(v).length > 0, { message: "envía al menos un campo" }),
  );
  const sets: string[] = [];
  const values: (string | null)[] = [];
  for (const [key, value] of Object.entries(input)) {
    if (value === undefined) continue;
    sets.push(`${key} = ?`);
    values.push(key === "report" ? (value === null ? null : toJson(value)) : (value as string | null));
  }
  const row = await guarded(() =>
    c.env.DB.prepare(`UPDATE backtests SET ${sets.join(", ")}, updated_at = ? WHERE id = ? RETURNING *`).bind(...values, nowIso(), id).first<Row>(),
  );
  if (!row) throw nf("Backtest");
  return c.json(backtestOut(row));
});

// --- latido y ajustes ---------------------------------------------------------------------

internal.post("/heartbeat", async (c) => {
  const input = await body(
    c,
    z.strictObject({
      component: z.string().regex(/^[a-z0-9_-]{1,64}$/, "componente inválido"),
      status: z.enum(["ok", "degraded", "error", "stopping"]).default("ok"),
      version: z.string().max(64).nullable().optional(),
      host: z.string().max(128).nullable().optional(),
      started_at: IsoDateTime.nullable().optional(),
      details: JsonObject.default({}),
    }),
  );
  const now = nowIso();
  const row = await c.env.DB.prepare(
    `INSERT INTO engine_heartbeats (component, status, version, details, host, started_at, last_seen_at)
     VALUES (?, ?, ?, ?, ?, ?, ?)
     ON CONFLICT (component) DO UPDATE SET status = excluded.status, version = excluded.version, details = excluded.details,
       host = excluded.host, started_at = COALESCE(excluded.started_at, engine_heartbeats.started_at), last_seen_at = excluded.last_seen_at
     RETURNING *`,
  )
    .bind(input.component, input.status, input.version ?? null, toJson(input.details), input.host ?? null, input.started_at ?? null, now)
    .first<Row>();
  return c.json(withJson(row as Row, ["details"]));
});

internal.get("/settings", async (c) => c.json(await readSettings(c.env.DB)));
