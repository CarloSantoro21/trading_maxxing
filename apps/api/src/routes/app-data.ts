/**
 * Endpoints que reemplazan las lecturas y escrituras directas del frontend a Supabase:
 *
 * - GET  /dashboard   (antes lib/dashboard/data.ts con supabase-js + Realtime)
 * - GET  /onboarding  y PUT /onboarding (antes lib/onboarding/repository.ts)
 * - GET  /equity-snapshots y /decisions (para refrescar el dashboard por polling)
 * - GET  /lab/reports y /lab/reports/{name} (mismo contrato que FastAPI)
 * - GET  /engine/status (último latido del motor de la Mac)
 */

import { Hono } from "hono";
import { z } from "zod";
import { GOAL_COLS, type Row, all, newId, nowIso, num, placeholders, tradeOut, userBotIds, withJson } from "../lib/db";
import { notFound } from "../lib/errors";
import { REPORT_NAME } from "../lib/lab-core";
import { getDetail, listSummaries } from "../lib/lab-reports";
import { levelDrawdown } from "../lib/risk";
import { IsoDateTime, Uuid, body, intQuery, parse, query } from "../lib/validation";
import { Capital, RiskLevel } from "./profiles";
import { FutureDate, TargetReturn } from "./goals";
import type { AppEnv } from "../types";

export const RECENT_TRADES = 20;
export const MAX_SNAPSHOTS = 2000;
const NO_SLEEVE = "sin_sleeve";

const TRADE_LIST_COLS =
  "id, bot_id, symbol, side, quantity, entry_price, exit_price, stop_loss, take_profit, fees, pnl, rationale, opened_at, closed_at";

export const appData = new Hono<AppEnv>();

appData.get("/dashboard", async (c) => {
  const db = c.env.DB;
  const profile = await db
    .prepare("SELECT id, capital, risk_level, max_drawdown FROM profiles WHERE user_id = ?")
    .bind(c.get("user").id)
    .first<Row>();
  const empty = { profile: null, bots: [], snapshots: [], recentTrades: [], openTrades: [], closed: {} };
  if (!profile) return c.json(empty);
  const summary = { id: profile.id, capital: num(profile.capital), risk_level: num(profile.risk_level), max_drawdown: profile.max_drawdown };
  const bots = await all<Row>(
    db
      .prepare("SELECT id, profile_id, name, status, started_at, stopped_at FROM bots WHERE profile_id = ? ORDER BY created_at")
      .bind(profile.id as string),
  );
  if (bots.length === 0) return c.json({ ...empty, profile: summary, bots });
  const ids = bots.map((b) => b.id as string);
  const inIds = placeholders(ids.length);
  const [snaps, recent, open, closed] = await db.batch<Row>([
    db
      .prepare(
        `SELECT bot_id, ts, equity, cash, positions_value, drawdown FROM equity_snapshots
         WHERE bot_id IN (${inIds}) ORDER BY ts DESC LIMIT ${MAX_SNAPSHOTS}`,
      )
      .bind(...ids),
    db.prepare(`SELECT ${TRADE_LIST_COLS} FROM trades WHERE bot_id IN (${inIds}) ORDER BY opened_at DESC LIMIT ${RECENT_TRADES}`).bind(...ids),
    db.prepare(`SELECT ${TRADE_LIST_COLS} FROM trades WHERE bot_id IN (${inIds}) AND closed_at IS NULL ORDER BY opened_at DESC`).bind(...ids),
    db
      .prepare(
        `SELECT id, pnl, json_extract(rationale, '$.sleeve') AS sleeve FROM trades
         WHERE bot_id IN (${inIds}) AND closed_at IS NOT NULL`,
      )
      .bind(...ids),
  ]);
  const closedMap: Record<string, { sleeve: string; pnl: number }> = {};
  for (const r of closed?.results ?? []) {
    closedMap[r.id as string] = { sleeve: (r.sleeve as string | null) || NO_SLEEVE, pnl: num(r.pnl) };
  }
  return c.json({
    profile: summary,
    bots,
    snapshots: [...(snaps?.results ?? [])].reverse(),
    recentTrades: (recent?.results ?? []).map(tradeOut),
    openTrades: (open?.results ?? []).map(tradeOut),
    closed: closedMap,
  });
});

appData.get("/equity-snapshots", async (c) => {
  const q = query(
    c,
    z.object({ bot_id: Uuid.optional(), since: IsoDateTime.optional(), limit: intQuery(1, MAX_SNAPSHOTS, MAX_SNAPSHOTS) }),
  );
  const ids = await userBotIds(c.env.DB, c.get("user").id);
  const wanted = q.bot_id ? ids.filter((id) => id === q.bot_id) : ids;
  if (wanted.length === 0) return c.json([]);
  const values: (string | number)[] = [...wanted];
  let where = `bot_id IN (${placeholders(wanted.length)})`;
  if (q.since) {
    where += " AND ts > ?";
    values.push(q.since);
  }
  const rows = await all<Row>(
    c.env.DB.prepare(
      `SELECT bot_id, ts, equity, cash, positions_value, drawdown FROM equity_snapshots WHERE ${where} ORDER BY ts DESC LIMIT ?`,
    ).bind(...values, q.limit),
  );
  return c.json(rows.reverse());
});

appData.get("/decisions", async (c) => {
  const q = query(c, z.object({ bot_id: Uuid.optional(), limit: intQuery(1, 500, 100) }));
  const ids = await userBotIds(c.env.DB, c.get("user").id);
  const wanted = q.bot_id ? ids.filter((id) => id === q.bot_id) : ids;
  if (wanted.length === 0) return c.json([]);
  const rows = await all<Row>(
    c.env.DB.prepare(
      `SELECT id, bot_id, strategy, symbol, kind, payload, decided_at, created_at FROM decisions
       WHERE bot_id IN (${placeholders(wanted.length)}) ORDER BY decided_at DESC LIMIT ?`,
    ).bind(...wanted, q.limit),
  );
  return c.json(rows.map((r) => withJson(r, ["payload"])));
});

// --- onboarding ------------------------------------------------------------------------

const OnboardingInput = z.strictObject({
  risk_level: RiskLevel,
  capital: Capital,
  goals: z.array(z.strictObject({ target_return: TargetReturn, deadline: FutureDate })).max(20),
});

appData.get("/onboarding", async (c) => {
  const profile = await c.env.DB.prepare("SELECT id, risk_level, capital, max_drawdown FROM profiles WHERE user_id = ?")
    .bind(c.get("user").id)
    .first<Row>();
  if (!profile) return c.json(null);
  const goals = await all<Row>(
    c.env.DB.prepare(`SELECT ${GOAL_COLS} FROM goals g WHERE g.profile_id = ? ORDER BY g.deadline`).bind(profile.id as string),
  );
  return c.json({ ...profile, goals: goals.map((g) => ({ target_return: g.target_return, deadline: g.deadline })) });
});

/**
 * Crea o actualiza el perfil (uno por usuario) y reemplaza sus metas, todo en un batch
 * atómico (antes eran tres llamadas a Supabase que podían quedar a medias).
 */
appData.put("/onboarding", async (c) => {
  const input = await body(c, OnboardingInput);
  const db = c.env.DB;
  const userId = c.get("user").id;
  const existing = await db.prepare("SELECT id FROM profiles WHERE user_id = ?").bind(userId).first<{ id: string }>();
  const profileId = existing?.id ?? newId();
  const now = nowIso();
  const dd = levelDrawdown(input.risk_level);
  await db.batch([
    existing
      ? db
          .prepare("UPDATE profiles SET risk_level = ?, capital = ?, max_drawdown = ?, updated_at = ? WHERE id = ?")
          .bind(input.risk_level, input.capital, dd, now, profileId)
      : db
          .prepare(
            "INSERT INTO profiles (id, user_id, risk_level, capital, max_drawdown, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
          )
          .bind(profileId, userId, input.risk_level, input.capital, dd, now, now),
    db.prepare("DELETE FROM goals WHERE profile_id = ?").bind(profileId),
    ...input.goals.map((g) =>
      db
        .prepare("INSERT INTO goals (id, profile_id, target_return, deadline, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)")
        .bind(newId(), profileId, g.target_return, g.deadline, now, now),
    ),
  ]);
  return c.json({ profileId, profile_id: profileId });
});

// --- laboratorio -----------------------------------------------------------------------

appData.get("/lab/reports", (c) => c.json(listSummaries()));

appData.get("/lab/reports/:name", (c) => {
  const name = parse(z.string().regex(REPORT_NAME, "nombre de reporte inválido"), c.req.param("name"), "path");
  const detail = getDetail(name);
  if (!detail) throw notFound("Reporte");
  return c.json(detail);
});

// --- motor -----------------------------------------------------------------------------

appData.get("/engine/status", async (c) => {
  const rows = await all<Row>(
    c.env.DB.prepare("SELECT component, status, version, details, host, started_at, last_seen_at FROM engine_heartbeats ORDER BY component"),
  );
  const staleSeconds = Number(c.env.ENGINE_STALE_SECONDS ?? "120") || 120;
  const now = Date.now();
  const components = rows.map((r) => {
    const age = (now - Date.parse(String(r.last_seen_at))) / 1000;
    return { ...withJson(r, ["details"]), last_seen_at: String(r.last_seen_at), age_seconds: Math.max(0, Math.round(age)), online: age <= staleSeconds };
  });
  const last = components.reduce<string | null>((acc, r) => {
    const seen = String(r.last_seen_at);
    return acc === null || seen > acc ? seen : acc;
  }, null);
  return c.json({
    online: components.some((r) => r.online),
    last_heartbeat_at: last,
    stale_after_seconds: staleSeconds,
    components,
  });
});
