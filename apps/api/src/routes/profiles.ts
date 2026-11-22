/** CRUD de perfiles de inversión (uno por usuario). Mismo contrato que /profiles de FastAPI. */

import { Hono } from "hono";
import { z } from "zod";
import { PROFILE_COLS, type Row, all, newId, nowIso, profileOut, resolveProfile } from "../lib/db";
import { conflict, fromDbError, notFound } from "../lib/errors";
import { MIN_CAPITAL, levelDrawdown } from "../lib/risk";
import { Num, body, pathUuid, requireSomeField } from "../lib/validation";
import type { AppEnv } from "../types";

export const RiskLevel = z.number().int().min(1).max(5);
export const Capital = Num.pipe(z.number().min(MIN_CAPITAL, `el capital mínimo es ${MIN_CAPITAL} USDT`));

export const ProfileCreate = z.strictObject({ risk_level: RiskLevel, capital: Capital });
const ProfileUpdate = z
  .strictObject({ risk_level: RiskLevel.nullable().optional(), capital: Capital.nullable().optional() })
  .superRefine(requireSomeField);

export const profiles = new Hono<AppEnv>();

profiles.get("/", async (c) => {
  const rows = await all<Row>(
    c.env.DB.prepare(`SELECT ${PROFILE_COLS} FROM profiles p WHERE p.user_id = ? ORDER BY p.created_at`).bind(c.get("user").id),
  );
  return c.json(rows.map(profileOut));
});

profiles.get("/me", async (c) => c.json(await resolveProfile(c.env.DB, c.get("user").id)));

/** Inserta el perfil del usuario; 409 si ya tiene uno. */
export async function insertProfile(db: D1Database, userId: string, riskLevel: number, capital: number): Promise<Row> {
  const now = nowIso();
  try {
    const row = await db
      .prepare(
        `INSERT INTO profiles (id, user_id, risk_level, capital, max_drawdown, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?)
         RETURNING id, user_id, horizon, risk_level, capital, max_drawdown, instruments, created_at, updated_at`,
      )
      .bind(newId(), userId, riskLevel, capital, levelDrawdown(riskLevel), now, now)
      .first<Row>();
    return profileOut(row as Row);
  } catch (err) {
    const mapped = fromDbError(err);
    if (mapped?.code === "unique_violation") throw conflict("Ya tienes un perfil de inversión; edítalo con PATCH");
    throw mapped ?? err;
  }
}

profiles.post("/", async (c) => {
  const input = await body(c, ProfileCreate);
  return c.json(await insertProfile(c.env.DB, c.get("user").id, input.risk_level, input.capital), 201);
});

profiles.get("/:id", async (c) => c.json(await resolveProfile(c.env.DB, c.get("user").id, pathUuid(c, "id"))));

profiles.patch("/:id", async (c) => {
  const id = pathUuid(c, "id");
  const input = await body(c, ProfileUpdate);
  const sets: string[] = [];
  const values: (string | number)[] = [];
  if (input.risk_level != null) {
    sets.push("risk_level = ?", "max_drawdown = ?");
    values.push(input.risk_level, levelDrawdown(input.risk_level));
  }
  if (input.capital != null) {
    sets.push("capital = ?");
    values.push(input.capital);
  }
  sets.push("updated_at = ?");
  values.push(nowIso());
  const row = await c.env.DB.prepare(
    `UPDATE profiles SET ${sets.join(", ")} WHERE id = ? AND user_id = ?
     RETURNING id, user_id, horizon, risk_level, capital, max_drawdown, instruments, created_at, updated_at`,
  )
    .bind(...values, id, c.get("user").id)
    .first<Row>();
  if (!row) throw notFound("Perfil");
  return c.json(profileOut(row));
});

profiles.delete("/:id", async (c) => {
  const id = pathUuid(c, "id");
  const res = await c.env.DB.prepare("DELETE FROM profiles WHERE id = ? AND user_id = ?").bind(id, c.get("user").id).run();
  if (!res.meta.changes) throw notFound("Perfil");
  return c.body(null, 204);
});
