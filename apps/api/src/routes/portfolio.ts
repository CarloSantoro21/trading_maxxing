/**
 * Portafolio y operaciones (mismo contrato que /portfolio y /trades de FastAPI).
 *
 * Cada bot es un sleeve: de cada uno se toma su última foto de equity_snapshots, sus
 * operaciones abiertas y el P&L realizado de las cerradas. Sin fotos, el portafolio vale
 * el capital del perfil en USDT.
 */

import { Hono } from "hono";
import { z } from "zod";
import { BOT_COLS, TRADE_COLS, type Row, all, num, placeholders, resolveProfile, tradeOut } from "../lib/db";
import { Symbol, Uuid, intQuery, query } from "../lib/validation";
import type { AppEnv } from "../types";

export const portfolio = new Hono<AppEnv>();

portfolio.get("/", async (c) => {
  const q = query(c, z.object({ profile_id: Uuid.optional() }));
  const db = c.env.DB;
  const profile = await resolveProfile(db, c.get("user").id, q.profile_id);
  const capital = num(profile.capital);
  const bots = await all<Row>(
    db.prepare(`SELECT ${BOT_COLS} FROM bots b WHERE b.profile_id = ? ORDER BY b.created_at`).bind(profile.id as string),
  );
  const ids = bots.map((b) => b.id as string);
  const snapshots = new Map<string, Row>();
  let openTrades: Row[] = [];
  const realized = new Map<string, number>();
  if (ids.length > 0) {
    const inIds = placeholders(ids.length);
    const [snapRows, openRows, closedRows] = await db.batch<Row>([
      db
        .prepare(
          `SELECT es.bot_id, es.ts, es.equity, es.cash, es.positions_value, es.drawdown FROM equity_snapshots es
           WHERE es.bot_id IN (${inIds}) AND es.ts = (SELECT MAX(ts) FROM equity_snapshots WHERE bot_id = es.bot_id)`,
        )
        .bind(...ids),
      db
        .prepare(`SELECT ${TRADE_COLS} FROM trades t WHERE t.bot_id IN (${inIds}) AND t.closed_at IS NULL ORDER BY t.opened_at DESC`)
        .bind(...ids),
      db
        .prepare(`SELECT bot_id, SUM(COALESCE(pnl, 0)) AS pnl FROM trades WHERE bot_id IN (${inIds}) AND closed_at IS NOT NULL GROUP BY bot_id`)
        .bind(...ids),
    ]);
    for (const s of snapRows?.results ?? []) snapshots.set(s.bot_id as string, s);
    openTrades = (openRows?.results ?? []).map(tradeOut);
    for (const r of closedRows?.results ?? []) realized.set(r.bot_id as string, num(r.pnl));
  }
  const openCount = new Map<string, number>();
  for (const t of openTrades) openCount.set(t.bot_id as string, (openCount.get(t.bot_id as string) ?? 0) + 1);

  const sleeves = bots.map((bot) => {
    const id = bot.id as string;
    const snap = snapshots.get(id);
    return {
      bot_id: id,
      name: bot.name,
      status: bot.status,
      equity: snap ? num(snap.equity) : null,
      cash: snap ? num(snap.cash) : null,
      positions_value: snap ? num(snap.positions_value) : null,
      drawdown: snap ? num(snap.drawdown) : null,
      as_of: snap ? snap.ts : null,
      realized_pnl: realized.get(id) ?? 0,
      open_trades: openCount.get(id) ?? 0,
    };
  });
  const snaps = [...snapshots.values()];
  const equity = snaps.length ? snaps.reduce((a, s) => a + num(s.equity), 0) : capital;
  const cash = snaps.length ? snaps.reduce((a, s) => a + num(s.cash), 0) : capital;
  const positionsValue = snaps.length ? snaps.reduce((a, s) => a + num(s.positions_value), 0) : 0;
  return c.json({
    profile_id: profile.id,
    capital,
    max_drawdown: num(profile.max_drawdown),
    equity,
    cash,
    positions_value: positionsValue,
    realized_pnl: [...realized.values()].reduce((a, b) => a + b, 0),
    total_return: capital ? (equity - capital) / capital : 0,
    sleeves,
    open_positions: openTrades,
  });
});

export const MAX_PAGE = 200;

export const trades = new Hono<AppEnv>();

trades.get("/", async (c) => {
  const q = query(
    c,
    z.object({
      bot_id: Uuid.optional(),
      symbol: Symbol.optional(),
      state: z.enum(["open", "closed"]).optional(),
      limit: intQuery(1, MAX_PAGE, 50),
      offset: intQuery(0, Number.MAX_SAFE_INTEGER, 0),
    }),
  );
  const where = ["p.user_id = ?"];
  const values: (string | number)[] = [c.get("user").id];
  if (q.bot_id) {
    where.push("t.bot_id = ?");
    values.push(q.bot_id);
  }
  if (q.symbol) {
    where.push("t.symbol = ?");
    values.push(q.symbol);
  }
  if (q.state === "open") where.push("t.closed_at IS NULL");
  if (q.state === "closed") where.push("t.closed_at IS NOT NULL");
  const from = `FROM trades t JOIN bots b ON b.id = t.bot_id JOIN profiles p ON p.id = b.profile_id WHERE ${where.join(" AND ")}`;
  const db = c.env.DB;
  const [items, count] = await db.batch<Row>([
    db.prepare(`SELECT ${TRADE_COLS} ${from} ORDER BY t.opened_at DESC, t.id DESC LIMIT ? OFFSET ?`).bind(...values, q.limit, q.offset),
    db.prepare(`SELECT COUNT(*) AS total ${from}`).bind(...values),
  ]);
  return c.json({
    items: (items?.results ?? []).map(tradeOut),
    total: num(count?.results?.[0]?.total),
    limit: q.limit,
    offset: q.offset,
  });
});
