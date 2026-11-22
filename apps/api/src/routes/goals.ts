/** CRUD de metas y su probabilidad (Monte Carlo #3). Mismo contrato que /goals de FastAPI. */

import { Hono } from "hono";
import { z } from "zod";
import { GOAL_COLS, type Row, all, newId, nowIso, num, ownedGoal, resolveProfile } from "../lib/db";
import { ApiError, notFound } from "../lib/errors";
import type { Horizon } from "../lib/allocator";
import { ProjectionError, currentEquity, loadStrategySeries, projectGoal } from "../lib/projection";
import { IsoDate, Num, Uuid, body, pathUuid, query, requireSomeField } from "../lib/validation";
import type { AppEnv } from "../types";

const todayUtc = (): string => new Date().toISOString().slice(0, 10);

export const TargetReturn = Num.pipe(z.number().gt(0, "el retorno objetivo debe ser mayor que 0"));
export const FutureDate = IsoDate.refine((d) => d >= todayUtc(), { message: "la fecha límite no puede estar en el pasado" });

const GoalCreate = z.strictObject({ profile_id: Uuid, target_return: TargetReturn, deadline: FutureDate });
const GoalUpdate = z
  .strictObject({ target_return: TargetReturn.nullable().optional(), deadline: IsoDate.nullable().optional() })
  .superRefine(requireSomeField);

export const goals = new Hono<AppEnv>();

goals.get("/", async (c) => {
  const q = query(c, z.object({ profile_id: Uuid.optional() }));
  const userId = c.get("user").id;
  const stmt = q.profile_id
    ? c.env.DB.prepare(
        `SELECT ${GOAL_COLS} FROM goals g JOIN profiles p ON p.id = g.profile_id
         WHERE p.user_id = ? AND g.profile_id = ? ORDER BY g.deadline`,
      ).bind(userId, q.profile_id)
    : c.env.DB.prepare(
        `SELECT ${GOAL_COLS} FROM goals g JOIN profiles p ON p.id = g.profile_id WHERE p.user_id = ? ORDER BY g.deadline`,
      ).bind(userId);
  return c.json(await all<Row>(stmt));
});

goals.post("/", async (c) => {
  const input = await body(c, GoalCreate);
  await resolveProfile(c.env.DB, c.get("user").id, input.profile_id);
  const now = nowIso();
  const row = await c.env.DB.prepare(
    `INSERT INTO goals (id, profile_id, target_return, deadline, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?) RETURNING id, profile_id, target_return, deadline, created_at, updated_at`,
  )
    .bind(newId(), input.profile_id, input.target_return, input.deadline, now, now)
    .first<Row>();
  return c.json(row, 201);
});

goals.get("/:id", async (c) => c.json(await ownedGoal(c.env.DB, c.get("user").id, pathUuid(c, "id"))));

goals.patch("/:id", async (c) => {
  const id = pathUuid(c, "id");
  const input = await body(c, GoalUpdate);
  await ownedGoal(c.env.DB, c.get("user").id, id);
  const sets: string[] = [];
  const values: (string | number)[] = [];
  if (input.target_return != null) {
    sets.push("target_return = ?");
    values.push(input.target_return);
  }
  if (input.deadline != null) {
    sets.push("deadline = ?");
    values.push(input.deadline);
  }
  sets.push("updated_at = ?");
  values.push(nowIso());
  const row = await c.env.DB.prepare(
    `UPDATE goals SET ${sets.join(", ")} WHERE id = ?
     RETURNING id, profile_id, target_return, deadline, created_at, updated_at`,
  )
    .bind(...values, id)
    .first<Row>();
  return c.json(row);
});

goals.delete("/:id", async (c) => {
  const id = pathUuid(c, "id");
  await ownedGoal(c.env.DB, c.get("user").id, id);
  await c.env.DB.prepare("DELETE FROM goals WHERE id = ?").bind(id).run();
  return c.body(null, 204);
});

goals.get("/:id/probability", async (c) => {
  const userId = c.get("user").id;
  const goal = await ownedGoal(c.env.DB, userId, pathUuid(c, "id"));
  const profile = await resolveProfile(c.env.DB, userId, goal.profile_id as string).catch(() => {
    throw notFound("Perfil");
  });
  const capital = num(profile.capital);
  const [startEquity, strategies] = await Promise.all([currentEquity(c.env.DB, profile), loadStrategySeries(c.env.DB)]);
  const today = todayUtc();
  const deadline = String(goal.deadline).slice(0, 10);
  try {
    const result = projectGoal({
      startEquity,
      targetEquity: capital * (1 + num(goal.target_return)),
      riskLevel: Number(profile.risk_level),
      horizon: (profile.horizon as Horizon) ?? "medium",
      today,
      deadline,
      strategies,
    });
    return c.json({
      ...result,
      goal_id: goal.id,
      profile_id: profile.id,
      target_return: num(goal.target_return),
      deadline,
      as_of: today,
      capital,
    });
  } catch (err) {
    if (err instanceof ProjectionError) throw new ApiError(422, err.message);
    throw err;
  }
});
