/**
 * Bots: alta, arranque, paro y paro de emergencia. Mismo contrato que /bots de FastAPI.
 *
 * Antes cada comando se publicaba en el stream tm:commands de Redis. El Worker no llega
 * al Redis de la Mac, así que ahora el cambio de estado del bot y el comando se escriben
 * juntos en D1 (un solo batch, atómico) y el motor los recoge con GET /internal/commands.
 * `command_id` es el id de la fila en `commands`. Ya no hay 503 por Redis caído.
 */

import { type Context, Hono } from "hono";
import { z } from "zod";
import { BOT_COLS, type Row, all, botOut, newId, nowIso, ownedBot, resolveProfile, toJson } from "../lib/db";
import { conflict } from "../lib/errors";
import { BotStatus, JsonObject, Uuid, body, pathUuid, query, readJson, parse, requireSomeField } from "../lib/validation";
import type { AppEnv, CurrentUser } from "../types";

const Name = z
  .string()
  .min(1)
  .max(100)
  .transform((s) => s.trim())
  .refine((s) => s.length > 0, { message: "el nombre no puede estar vacío" });

const BotCreate = z.strictObject({
  name: Name,
  profile_id: Uuid.nullable().optional(),
  strategy_config: JsonObject.optional().default({}),
});

const BotUpdate = z
  .strictObject({ name: Name.nullable().optional(), strategy_config: JsonObject.nullable().optional() })
  .superRefine(requireSomeField);

const KillRequest = z.strictObject({ reason: z.string().max(500).default("paro de emergencia manual") });

const CAN_START = new Set(["stopped", "paused", "killed"]);
const CAN_STOP = new Set(["running", "paused"]);

type Command = "start" | "stop" | "kill";

const cannot = (bot: Row, action: string) => conflict(`No se puede ${action} un bot en estado ${String(bot.status)}`);

/** Actualiza el bot y encola el comando en el mismo batch (transacción de D1). */
async function applyCommand(
  db: D1Database,
  user: CurrentUser,
  bot: Row,
  command: Command,
  values: Record<string, string | null>,
  reason = "",
  payload: Record<string, unknown> = {},
): Promise<Row> {
  const commandId = newId();
  const now = nowIso();
  const keys = Object.keys(values);
  const [updated] = await db.batch<Row>([
    db
      .prepare(`UPDATE bots SET ${keys.map((k) => `${k} = ?`).join(", ")}, updated_at = ? WHERE id = ? RETURNING *`)
      .bind(...keys.map((k) => values[k] ?? null), now, bot.id as string),
    db
      .prepare("INSERT INTO commands (id, type, bot_id, user_id, reason, payload, issued_at) VALUES (?, ?, ?, ?, ?, ?, ?)")
      .bind(commandId, command, bot.id as string, user.id, reason, toJson(payload), now),
  ]);
  const row = updated?.results?.[0] as Row;
  return { bot: botOut(row), command, command_id: commandId };
}

async function kill(db: D1Database, user: CurrentUser, bot: Row, reason: string): Promise<Row> {
  return applyCommand(db, user, bot, "kill", { status: "killed", stopped_at: nowIso() }, reason, { close_positions: true });
}

async function killReason(c: Context<AppEnv>): Promise<string> {
  const raw = await readJson(c);
  return parse(KillRequest, raw ?? {}, "body").reason;
}

export const bots = new Hono<AppEnv>();

bots.get("/", async (c) => {
  const q = query(c, z.object({ profile_id: Uuid.optional(), status: BotStatus.optional() }));
  const where = ["p.user_id = ?"];
  const values: string[] = [c.get("user").id];
  if (q.profile_id) {
    where.push("b.profile_id = ?");
    values.push(q.profile_id);
  }
  if (q.status) {
    where.push("b.status = ?");
    values.push(q.status);
  }
  const rows = await all<Row>(
    c.env.DB.prepare(
      `SELECT ${BOT_COLS} FROM bots b JOIN profiles p ON p.id = b.profile_id WHERE ${where.join(" AND ")} ORDER BY b.created_at`,
    ).bind(...values),
  );
  return c.json(rows.map(botOut));
});

bots.post("/", async (c) => {
  const input = await body(c, BotCreate);
  const profile = await resolveProfile(c.env.DB, c.get("user").id, input.profile_id);
  const now = nowIso();
  const row = await c.env.DB.prepare(
    `INSERT INTO bots (id, profile_id, name, strategy_config, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?) RETURNING *`,
  )
    .bind(newId(), profile.id as string, input.name, toJson(input.strategy_config), now, now)
    .first<Row>();
  return c.json(botOut(row as Row), 201);
});

bots.post("/kill-all", async (c) => {
  const reason = await killReason(c);
  const user = c.get("user");
  const rows = await all<Row>(
    c.env.DB.prepare(
      `SELECT ${BOT_COLS} FROM bots b JOIN profiles p ON p.id = b.profile_id
       WHERE p.user_id = ? AND b.status <> 'killed' ORDER BY b.created_at`,
    ).bind(user.id),
  );
  const killed: Row[] = [];
  for (const bot of rows) killed.push(await kill(c.env.DB, user, bot, reason));
  return c.json({ killed });
});

bots.get("/:id", async (c) => c.json(await ownedBot(c.env.DB, c.get("user").id, pathUuid(c, "id"))));

bots.patch("/:id", async (c) => {
  const id = pathUuid(c, "id");
  const input = await body(c, BotUpdate);
  await ownedBot(c.env.DB, c.get("user").id, id);
  const sets: string[] = [];
  const values: string[] = [];
  if (input.name != null) {
    sets.push("name = ?");
    values.push(input.name);
  }
  if (input.strategy_config != null) {
    sets.push("strategy_config = ?");
    values.push(toJson(input.strategy_config));
  }
  const row = await c.env.DB.prepare(`UPDATE bots SET ${sets.join(", ")}, updated_at = ? WHERE id = ? RETURNING *`)
    .bind(...values, nowIso(), id)
    .first<Row>();
  return c.json(botOut(row as Row));
});

bots.delete("/:id", async (c) => {
  const id = pathUuid(c, "id");
  const bot = await ownedBot(c.env.DB, c.get("user").id, id);
  if (bot.status === "running" || bot.status === "paused") throw cannot(bot, "borrar");
  await c.env.DB.prepare("DELETE FROM bots WHERE id = ?").bind(id).run();
  return c.body(null, 204);
});

bots.post("/:id/start", async (c) => {
  const user = c.get("user");
  const bot = await ownedBot(c.env.DB, user.id, pathUuid(c, "id"));
  if (!CAN_START.has(String(bot.status))) throw cannot(bot, "arrancar");
  const result = await applyCommand(c.env.DB, user, bot, "start", { status: "running", started_at: nowIso(), stopped_at: null }, "", {
    strategy_config: bot.strategy_config ?? {},
  });
  return c.json(result);
});

bots.post("/:id/stop", async (c) => {
  const user = c.get("user");
  const bot = await ownedBot(c.env.DB, user.id, pathUuid(c, "id"));
  if (!CAN_STOP.has(String(bot.status))) throw cannot(bot, "detener");
  return c.json(await applyCommand(c.env.DB, user, bot, "stop", { status: "stopped", stopped_at: nowIso() }));
});

bots.post("/:id/kill", async (c) => {
  const user = c.get("user");
  const id = pathUuid(c, "id");
  const reason = await killReason(c);
  const bot = await ownedBot(c.env.DB, user.id, id);
  return c.json(await kill(c.env.DB, user, bot, reason));
});
