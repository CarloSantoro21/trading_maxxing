/** Estrategias (admin vs member), administración de usuarios y ajustes, laboratorio y APM. */

import { env } from "cloudflare:test";
import { beforeAll, describe, expect, it } from "vitest";
import { ADMIN, MEMBER, call, ensureStrategy, iso, json, seedBase } from "./helpers";

beforeAll(async () => {
  await seedBase();
  await ensureStrategy("alpha", "shadow", null, 10);
  await ensureStrategy("beta", "shadow", null, 2);
  await ensureStrategy("gamma", "active");
  await ensureStrategy("delta", "lab");
});

describe("estrategias", () => {
  it("cualquier usuario lista y lee, con shadow_days_clean", async () => {
    const list = await json(await call("/api/strategies", { as: MEMBER.email }));
    const alpha = list.find((s: { name: string }) => s.name === "alpha");
    expect(alpha.state).toBe("shadow");
    expect(alpha.shadow_days_clean).toBeGreaterThan(9);
    const gamma = await json(await call("/api/strategies/gamma", { as: MEMBER.email }));
    expect(gamma).toMatchObject({ state: "active", shadow_days_clean: null, params: {}, live_metrics: {} });
    expect((await call("/api/strategies/nope", { as: MEMBER.email })).status).toBe(404);
  });

  it("overview para la página /strategies", async () => {
    const o = await json(await call("/api/strategies/overview", { as: MEMBER.email }));
    expect(o.isAdmin).toBe(false);
    expect(o.strategies.length).toBeGreaterThanOrEqual(4);
    expect(o.transitions.length).toBeGreaterThan(0);
    expect(Array.isArray(o.equity)).toBe(true);
    expect((await json(await call("/api/strategies/overview", { as: ADMIN.email }))).isAdmin).toBe(true);
    const hist = await json(await call("/api/strategies/alpha/transitions", { as: MEMBER.email }));
    expect(hist.map((t: { to_state: string }) => t.to_state)).toEqual(["lab", "shadow"]);
  });

  it("un member no puede promover ni retirar (403)", async () => {
    const res = await call("/api/strategies/alpha/promote", { as: MEMBER.email, method: "POST", body: { reason: "quiero" } });
    expect(res.status).toBe(403);
    expect((await call("/api/strategies/alpha/retire", { as: MEMBER.email, method: "POST", body: { reason: "x" } })).status).toBe(403);
  });

  it("el admin promueve tras 7 días en shadow", async () => {
    const res = await call("/api/strategies/alpha/promote", { as: ADMIN.email, method: "POST", body: { reason: "  lista  " } });
    expect(res.status).toBe(200);
    const t = await json(res);
    expect(t).toMatchObject({ from_state: "shadow", to_state: "active", actor: ADMIN.id, reason: "lista", strategy: { state: "active" } });
  });

  it("409 con menos de 7 días en shadow, desde lab o ya retirada", async () => {
    const short = await call("/api/strategies/beta/promote", { as: ADMIN.email, method: "POST", body: { reason: "x" } });
    expect(short.status).toBe(409);
    expect((await json(short)).detail).toContain("se requieren al menos 7");
    expect((await call("/api/strategies/delta/promote", { as: ADMIN.email, method: "POST", body: { reason: "x" } })).status).toBe(409);
    expect((await call("/api/strategies/delta/retire", { as: ADMIN.email, method: "POST", body: { reason: "fuera" } })).status).toBe(200);
    expect((await call("/api/strategies/delta/retire", { as: ADMIN.email, method: "POST", body: { reason: "otra vez" } })).status).toBe(409);
  });

  it("clean_since reinicia los días en shadow", async () => {
    await env.DB.prepare("UPDATE strategies SET live_metrics = ? WHERE name = 'beta'").bind(JSON.stringify({ shadow: { clean_since: iso(-1) } })).run();
    const beta = await json(await call("/api/strategies/beta", { as: MEMBER.email }));
    expect(beta.shadow_days_clean).toBeLessThan(1.1);
  });

  it("422 sin motivo", async () => {
    expect((await call("/api/strategies/beta/retire", { as: ADMIN.email, method: "POST", body: { reason: "   " } })).status).toBe(422);
  });

  it("reference-equity", async () => {
    const id = await ensureStrategy("gamma", "active");
    await env.DB.prepare(
      "INSERT INTO reference_equity (strategy_id, started_at, day, state, equity, cash) VALUES (?, ?, ?, 'active', 1000, 1000)",
    )
      .bind(id, iso(-5), iso(-1).slice(0, 10))
      .run();
    expect((await json(await call(`/api/strategies/reference-equity?strategy_id=${id}`, { as: MEMBER.email }))).length).toBe(1);
    expect((await json(await call("/api/strategies/reference-equity", { as: MEMBER.email }))).length).toBeGreaterThan(0);
  });
});

describe("administración", () => {
  let newUserId: string;

  it("un member recibe 403 en /api/admin/*", async () => {
    for (const path of ["/api/admin/users", "/api/admin/settings"]) {
      expect((await call(path, { as: MEMBER.email })).status).toBe(403);
    }
    expect((await call("/api/admin/debug/error", { as: MEMBER.email, method: "POST" })).status).toBe(403);
  });

  it("alta, cambio de rol y baja de usuarios", async () => {
    const res = await call("/api/admin/users", { as: ADMIN.email, method: "POST", body: { email: "  Nueva@Test.dev " } });
    expect(res.status).toBe(201);
    const user = await json(res);
    newUserId = user.id;
    expect(user).toMatchObject({ email: "nueva@test.dev", role: "member" });
    expect((await call("/api/admin/users", { as: ADMIN.email, method: "POST", body: { email: "nueva@test.dev" } })).status).toBe(409);
    expect((await call("/api/admin/users", { as: ADMIN.email, method: "POST", body: { email: "mal" } })).status).toBe(422);
    const list = await json(await call("/api/admin/users", { as: ADMIN.email }));
    expect(list.some((u: { email: string }) => u.email === "nueva@test.dev")).toBe(true);
    const promoted = await json(await call(`/api/admin/users/${newUserId}`, { as: ADMIN.email, method: "PATCH", body: { role: "admin" } }));
    expect(promoted.role).toBe("admin");
    expect((await call(`/api/admin/users/${ADMIN.id}`, { as: ADMIN.email, method: "PATCH", body: { role: "member" } })).status).toBe(409);
    expect((await call(`/api/admin/users/${ADMIN.id}`, { as: ADMIN.email, method: "DELETE" })).status).toBe(409);
    expect((await call(`/api/admin/users/${newUserId}`, { as: ADMIN.email, method: "DELETE" })).status).toBe(204);
    expect((await call(`/api/admin/users/${newUserId}`, { as: ADMIN.email, method: "DELETE" })).status).toBe(404);
    expect((await call(`/api/admin/users/${newUserId}`, { as: ADMIN.email, method: "PATCH", body: { role: "member" } })).status).toBe(404);
  });

  it("ajustes: apagados por defecto; solo el admin los cambia", async () => {
    expect(await json(await call("/api/settings", { as: MEMBER.email }))).toEqual({ news_enabled: false, explainer_enabled: false });
    const updated = await json(await call("/api/admin/settings", { as: ADMIN.email, method: "PUT", body: { news_enabled: true } }));
    expect(updated).toEqual({ news_enabled: true, explainer_enabled: false });
    expect((await json(await call("/api/admin/settings", { as: ADMIN.email }))).news_enabled).toBe(true);
    expect((await call("/api/admin/settings", { as: ADMIN.email, method: "PUT", body: { otro: true } })).status).toBe(422);
    expect((await call("/api/admin/settings", { as: ADMIN.email, method: "PUT", body: {} })).status).toBe(422);
    await call("/api/admin/settings", { as: ADMIN.email, method: "PUT", body: { news_enabled: false } });
  });

  it("error de demo para APM: 500 con request_id", async () => {
    const res = await call("/api/admin/debug/error", { as: ADMIN.email, method: "POST" });
    expect(res.status).toBe(500);
    const body = await json(res);
    expect(body.detail).toBe("Error interno del servidor");
    expect(body.request_id).toBe(res.headers.get("X-Request-Id"));
  });
});

describe("laboratorio", () => {
  it("lista los reportes empaquetados", async () => {
    const list = await json(await call("/api/lab/reports", { as: MEMBER.email }));
    expect(list.map((r: { name: string }) => r.name)).toEqual(["intraday_momentum", "trend", "trend_vol"]);
    expect(list[0]).toHaveProperty("approved");
    expect(list[0]).not.toHaveProperty("windows");
  });

  it("detalle con histograma y 404/422", async () => {
    const d = await json(await call("/api/lab/reports/trend", { as: MEMBER.email }));
    expect(d.drawdown_histogram.bins).toHaveLength(30);
    expect(d.equity.length).toBeGreaterThan(1000);
    expect(d.windows.length).toBeGreaterThan(0);
    expect((await call("/api/lab/reports/nope", { as: MEMBER.email })).status).toBe(404);
    expect((await call("/api/lab/reports/Mal-Nombre", { as: MEMBER.email })).status).toBe(422);
  });
});

describe("CORS", () => {
  it("permite los orígenes configurados", async () => {
    const res = await call("/api/health", { headers: { Origin: "http://localhost:3000" } });
    expect(res.headers.get("Access-Control-Allow-Origin")).toBe("http://localhost:3000");
    const other = await call("/api/health", { headers: { Origin: "https://evil.example" } });
    expect(other.headers.get("Access-Control-Allow-Origin")).toBeNull();
  });
});
