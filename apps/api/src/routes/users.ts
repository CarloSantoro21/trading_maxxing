/**
 * Usuario actual, ajustes y administración (usuarios, ajustes y el error de demo para APM).
 *
 * Con Cloudflare Access ya no hay contraseñas: dar de alta a alguien es agregar su correo
 * a la tabla users (y a la allowlist de Access). POST /admin/users ya no devuelve
 * `temporary_password`; la persona entra con el One-time PIN que le manda Access.
 */

import { Hono } from "hono";
import { z } from "zod";
import { requireAdmin } from "../auth/middleware";
import { PROFILE_COLS, type Row, all, newId, nowIso, profileOut } from "../lib/db";
import { ApiError, conflict, fromDbError, notFound } from "../lib/errors";
import { log } from "../lib/log";
import { Uuid, body, parse } from "../lib/validation";
import type { AppEnv } from "../types";

const Role = z.enum(["admin", "member"]);
const Email = z
  .string()
  .trim()
  .toLowerCase()
  .min(3)
  .max(320)
  .regex(/^[^@\s]+@[^@\s]+\.[^@\s]+$/, "correo inválido");

const UserCreate = z.strictObject({ email: Email, role: Role.default("member") });
const UserUpdate = z.strictObject({ role: Role });

/** Ajustes conocidos y su tipo. Un ajuste desconocido da 422. */
export const SETTINGS_SCHEMA = z.strictObject({
  news_enabled: z.boolean().optional(),
  explainer_enabled: z.boolean().optional(),
});

export async function readSettings(db: D1Database): Promise<Record<string, unknown>> {
  const rows = await all<{ key: string; value: string }>(db.prepare("SELECT key, value FROM settings ORDER BY key"));
  const out: Record<string, unknown> = { news_enabled: false, explainer_enabled: false };
  for (const r of rows) {
    try {
      out[r.key] = JSON.parse(r.value) as unknown;
    } catch {
      out[r.key] = null;
    }
  }
  return out;
}

export const users = new Hono<AppEnv>();

users.get("/me", async (c) => {
  const user = c.get("user");
  const profile = await c.env.DB.prepare(`SELECT ${PROFILE_COLS} FROM profiles p WHERE p.user_id = ?`).bind(user.id).first<Row>();
  return c.json({ id: user.id, email: user.email, role: user.role, profile: profile ? profileOut(profile) : null });
});

users.get("/settings", async (c) => c.json(await readSettings(c.env.DB)));

// --- admin -----------------------------------------------------------------------------

export const admin = new Hono<AppEnv>();
admin.use("*", requireAdmin);

admin.get("/users", async (c) => {
  const rows = await all<Row>(c.env.DB.prepare("SELECT id, email, role, created_at, updated_at FROM users ORDER BY email"));
  return c.json(rows);
});

admin.post("/users", async (c) => {
  const input = await body(c, UserCreate);
  const now = nowIso();
  try {
    const row = await c.env.DB.prepare(
      "INSERT INTO users (id, email, role, created_at, updated_at) VALUES (?, ?, ?, ?, ?) RETURNING id, email, role, created_at, updated_at",
    )
      .bind(newId(), input.email, input.role, now, now)
      .first<Row>();
    log("info", "usuario dado de alta", { request_id: c.get("requestId"), by: c.get("user").email, email: input.email, role: input.role });
    return c.json(row, 201);
  } catch (err) {
    const mapped = fromDbError(err);
    if (mapped?.code === "unique_violation") throw conflict(`ya existe un usuario con el correo ${input.email}`);
    throw mapped ?? err;
  }
});

admin.patch("/users/:id", async (c) => {
  const id = parse(Uuid, c.req.param("id"), "path");
  const input = await body(c, UserUpdate);
  if (id === c.get("user").id && input.role !== "admin") {
    throw conflict("No puedes quitarte el rol de admin a ti mismo");
  }
  const row = await c.env.DB.prepare("UPDATE users SET role = ?, updated_at = ? WHERE id = ? RETURNING id, email, role, created_at, updated_at")
    .bind(input.role, nowIso(), id)
    .first<Row>();
  if (!row) throw notFound("Usuario");
  return c.json(row);
});

admin.delete("/users/:id", async (c) => {
  const id = parse(Uuid, c.req.param("id"), "path");
  if (id === c.get("user").id) throw conflict("No puedes borrarte a ti mismo");
  const res = await c.env.DB.prepare("DELETE FROM users WHERE id = ?").bind(id).run();
  if (!res.meta.changes) throw notFound("Usuario");
  return c.body(null, 204);
});

admin.get("/settings", async (c) => c.json(await readSettings(c.env.DB)));

admin.put("/settings", async (c) => {
  const input = await body(c, SETTINGS_SCHEMA);
  const entries = Object.entries(input).filter(([, v]) => v !== undefined);
  if (entries.length === 0) throw new ApiError(422, "envía al menos un ajuste");
  const now = nowIso();
  const by = c.get("user").email;
  await c.env.DB.batch(
    entries.map(([key, value]) =>
      c.env.DB.prepare(
        `INSERT INTO settings (key, value, updated_by, updated_at) VALUES (?, ?, ?, ?)
         ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_by = excluded.updated_by, updated_at = excluded.updated_at`,
      ).bind(key, JSON.stringify(value), by, now),
    ),
  );
  log("info", "ajustes actualizados", { request_id: c.get("requestId"), by, settings: Object.fromEntries(entries) });
  return c.json(await readSettings(c.env.DB));
});

/** Error controlado para la demo de APM: deja un log de error y una excepción con traza. */
export class DemoError extends Error {
  override name = "DemoError";
}

admin.post("/debug/error", (c) => {
  const user = c.get("user");
  log("error", "error de demostración para APM (solicitado por un admin)", {
    request_id: c.get("requestId"),
    user: user.email,
    route: "/api/admin/debug/error",
    demo: true,
  });
  throw new DemoError(`Error de demostración para APM (request ${c.get("requestId")})`);
});
