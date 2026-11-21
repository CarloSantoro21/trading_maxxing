/** Utilidades de prueba: app, peticiones como usuario o como motor, y datos base. */

import { env } from "cloudflare:test";
import { createApp } from "../src/app";
import type { Bindings } from "../src/types";

export const app = createApp();

export const ADMIN = { id: "aaaaaaaa-0000-0000-0000-000000000001", email: "admin@test.dev" };
export const MEMBER = { id: "bbbbbbbb-0000-0000-0000-000000000002", email: "member@test.dev" };
export const OTHER = { id: "cccccccc-0000-0000-0000-000000000003", email: "other@test.dev" };
export const SERVICE_TOKEN = "test-service-token";

export function testEnv(overrides: Partial<Bindings> = {}): Bindings {
  return { ...env, AUTH_MODE: "dev", ENVIRONMENT: "dev", DEV_USER_EMAIL: ADMIN.email, SERVICE_TOKEN, ...overrides } as Bindings;
}

export interface CallOptions {
  as?: string | null;
  method?: string;
  body?: unknown;
  headers?: Record<string, string>;
  env?: Partial<Bindings>;
}

export async function call(path: string, opts: CallOptions = {}): Promise<Response> {
  const headers: Record<string, string> = { ...(opts.headers ?? {}) };
  if (opts.as) headers["X-Dev-User"] = opts.as;
  let body: string | undefined;
  if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = typeof opts.body === "string" ? opts.body : JSON.stringify(opts.body);
  }
  return app.request(path, { method: opts.method ?? "GET", headers, body }, testEnv(opts.env));
}

export async function json<T = any>(res: Response): Promise<T> {
  return (await res.json()) as T;
}

export function service(path: string, opts: Omit<CallOptions, "as"> = {}): Promise<Response> {
  return call(path, { ...opts, headers: { "X-Service-Token": SERVICE_TOKEN, ...(opts.headers ?? {}) } });
}

export const iso = (offsetDays = 0): string => new Date(Date.now() + offsetDays * 86_400_000).toISOString();
export const day = (offsetDays = 0): string => iso(offsetDays).slice(0, 10);

/** Usuarios, estrategias y un bot del admin. Idempotente. */
export async function seedBase(): Promise<void> {
  const db = env.DB;
  await db.batch([
    db.prepare("INSERT OR IGNORE INTO users (id, email, role) VALUES (?, ?, 'admin')").bind(ADMIN.id, ADMIN.email),
    db.prepare("INSERT OR IGNORE INTO users (id, email, role) VALUES (?, ?, 'member')").bind(MEMBER.id, MEMBER.email),
    db.prepare("INSERT OR IGNORE INTO users (id, email, role) VALUES (?, ?, 'member')").bind(OTHER.id, OTHER.email),
  ]);
}

/** Crea el perfil del usuario (si no existe) y devuelve su id. */
export async function ensureProfile(email: string, capital = 10000, risk = 3): Promise<string> {
  const res = await call("/api/profiles/me", { as: email });
  if (res.status === 200) return (await json(res)).id;
  const created = await call("/api/profiles", { as: email, method: "POST", body: { risk_level: risk, capital } });
  return (await json(created)).id;
}

/** Inserta una estrategia en `state` recorriendo transiciones válidas. */
export async function ensureStrategy(name: string, state: string, labReport: unknown = null, shadowDaysAgo = 10): Promise<string> {
  const db = env.DB;
  const existing = await db.prepare("SELECT id, state FROM strategies WHERE name = ?").bind(name).first<{ id: string; state: string }>();
  if (existing) return existing.id;
  const id = crypto.randomUUID();
  const created = iso(-30);
  await db
    .prepare("INSERT INTO strategies (id, name, lab_report, created_at, state_changed_at) VALUES (?, ?, ?, ?, ?)")
    .bind(id, name, labReport ? JSON.stringify(labReport) : null, created, created)
    .run();
  const path: Record<string, string[]> = {
    research: [],
    lab: ["lab"],
    shadow: ["lab", "shadow"],
    active: ["lab", "shadow", "active"],
    probation: ["lab", "shadow", "active", "probation"],
    retired: ["retired"],
  };
  let from = "research";
  const steps = path[state] ?? [];
  for (let i = 0; i < steps.length; i++) {
    const to = steps[i]!;
    const when = to === "shadow" ? iso(-shadowDaysAgo) : iso(-(steps.length - i) - 20);
    const actor = to === "active" ? ADMIN.id : "system";
    await db
      .prepare("INSERT INTO strategy_transitions (strategy_id, from_state, to_state, actor, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)")
      .bind(id, from, to, actor, "prueba", to === "shadow" ? when : iso(-25 + i))
      .run();
    from = to;
  }
  return id;
}
