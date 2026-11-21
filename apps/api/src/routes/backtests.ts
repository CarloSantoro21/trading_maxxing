/**
 * Backtests (mismo contrato que /backtests de FastAPI). El usuario los encola en estado
 * `queued`; el motor de la Mac los toma con GET /internal/backtests?status=queued y escribe
 * el resultado con PATCH /internal/backtests/{id}. Ya no hay stream tm:backtests.
 */

import { Hono } from "hono";
import { z } from "zod";
import { BACKTEST_COLS, type Row, all, backtestOut, newId, nowIso, resolveProfile, toJson } from "../lib/db";
import { notFound } from "../lib/errors";
import { BacktestStatus, IsoDate, Num, Symbol, Timeframe, Uuid, body, intQuery, pathUuid, query } from "../lib/validation";
import type { AppEnv } from "../types";

const BacktestConfig = z
  .looseObject({
    symbols: z
      .array(Symbol)
      .min(1)
      .refine((v) => new Set(v).size === v.length, { message: "los instrumentos no deben repetirse" }),
    timeframe: Timeframe.default("15m"),
    start: IsoDate,
    end: IsoDate,
    mode: z.enum(["in_sample", "out_of_sample", "walk_forward"]).default("walk_forward"),
    strategies: z.array(z.string()).default([]),
    initial_capital: Num.pipe(z.number().gt(0)).nullable().optional(),
  })
  .refine((v) => v.end > v.start, { message: "end debe ser posterior a start", path: ["end"] });

const BacktestCreate = z.strictObject({ profile_id: Uuid.nullable().optional(), config: BacktestConfig });

export const backtests = new Hono<AppEnv>();

backtests.get("/", async (c) => {
  const q = query(c, z.object({ status: BacktestStatus.optional(), limit: intQuery(1, 200, 50) }));
  const where = ["p.user_id = ?"];
  const values: (string | number)[] = [c.get("user").id];
  if (q.status) {
    where.push("bt.status = ?");
    values.push(q.status);
  }
  const rows = await all<Row>(
    c.env.DB.prepare(
      `SELECT ${BACKTEST_COLS} FROM backtests bt JOIN profiles p ON p.id = bt.profile_id
       WHERE ${where.join(" AND ")} ORDER BY bt.created_at DESC LIMIT ?`,
    ).bind(...values, q.limit),
  );
  return c.json(rows.map(backtestOut));
});

backtests.post("/", async (c) => {
  const input = await body(c, BacktestCreate);
  const profile = await resolveProfile(c.env.DB, c.get("user").id, input.profile_id);
  // Igual que model_dump(exclude_none=True): no se guardan los campos en null.
  const config = Object.fromEntries(Object.entries(input.config).filter(([, v]) => v !== null && v !== undefined));
  const now = nowIso();
  const row = await c.env.DB.prepare(
    `INSERT INTO backtests (id, profile_id, status, config, created_at, updated_at)
     VALUES (?, ?, 'queued', ?, ?, ?) RETURNING *`,
  )
    .bind(newId(), profile.id as string, toJson(config), now, now)
    .first<Row>();
  return c.json(backtestOut(row as Row), 202);
});

backtests.get("/:id", async (c) => {
  const row = await c.env.DB.prepare(
    `SELECT ${BACKTEST_COLS} FROM backtests bt JOIN profiles p ON p.id = bt.profile_id WHERE bt.id = ? AND p.user_id = ?`,
  )
    .bind(pathUuid(c, "id"), c.get("user").id)
    .first<Row>();
  if (!row) throw notFound("Backtest");
  return c.json(backtestOut(row));
});
