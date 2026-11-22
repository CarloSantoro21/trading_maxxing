/**
 * Perfiles, metas, bots, portafolio, trades y backtests: el filtro por usuario que antes
 * hacía RLS. Cada usuario solo ve y modifica lo suyo (lo ajeno es 404).
 */

import { env } from "cloudflare:test";
import { beforeAll, describe, expect, it } from "vitest";
import { ADMIN, MEMBER, OTHER, call, day, ensureProfile, iso, json, seedBase } from "./helpers";

let memberProfile: string;
let otherProfile: string;

beforeAll(async () => {
  await seedBase();
  memberProfile = await ensureProfile(MEMBER.email, 5000, 2);
  otherProfile = await ensureProfile(OTHER.email, 20000, 4);
});

describe("perfiles", () => {
  it("crea con max_drawdown derivado y valores por defecto", async () => {
    const res = await call("/api/profiles/me", { as: MEMBER.email });
    const p = await json(res);
    expect(p).toMatchObject({ id: memberProfile, risk_level: 2, capital: 5000, max_drawdown: 0.15, horizon: "medium" });
    expect(p.instruments).toEqual(["BTCUSDT", "ETHUSDT"]);
  });

  it("409 si ya tiene perfil", async () => {
    const res = await call("/api/profiles", { as: MEMBER.email, method: "POST", body: { risk_level: 3, capital: "1000" } });
    expect(res.status).toBe(409);
  });

  it("422 con capital menor al mínimo, campos extra o JSON inválido", async () => {
    expect((await call("/api/profiles", { as: ADMIN.email, method: "POST", body: { risk_level: 3, capital: 100 } })).status).toBe(422);
    expect(
      (await call("/api/profiles", { as: ADMIN.email, method: "POST", body: { risk_level: 3, capital: 1000, horizon: "long" } })).status,
    ).toBe(422);
    const bad = await call("/api/profiles", { as: ADMIN.email, method: "POST", body: "{no json" });
    expect(bad.status).toBe(422);
    expect((await json(bad)).detail[0].type).toBe("json_invalid");
  });

  it("404 sin perfil y mensaje claro", async () => {
    await env.DB.prepare("INSERT OR IGNORE INTO users (id, email) VALUES (?, ?)").bind("dddddddd-0000-0000-0000-000000000004", "sinperfil@test.dev").run();
    const res = await call("/api/profiles/me", { as: "sinperfil@test.dev" });
    expect(res.status).toBe(404);
    expect((await json(res)).detail).toContain("POST /profiles");
  });

  it("lista solo los propios", async () => {
    const list = await json(await call("/api/profiles", { as: MEMBER.email }));
    expect(list.map((p: { id: string }) => p.id)).toEqual([memberProfile]);
  });

  it("no ve ni edita el perfil ajeno", async () => {
    expect((await call(`/api/profiles/${otherProfile}`, { as: MEMBER.email })).status).toBe(404);
    const patch = await call(`/api/profiles/${otherProfile}`, { as: MEMBER.email, method: "PATCH", body: { capital: 999999 } });
    expect(patch.status).toBe(404);
    expect((await call(`/api/profiles/${otherProfile}`, { as: MEMBER.email, method: "DELETE" })).status).toBe(404);
  });

  it("PATCH recalcula max_drawdown y valida", async () => {
    const res = await call(`/api/profiles/${memberProfile}`, { as: MEMBER.email, method: "PATCH", body: { risk_level: 5 } });
    expect(await json(res)).toMatchObject({ risk_level: 5, max_drawdown: 0.4 });
    const cap = await json(await call(`/api/profiles/${memberProfile}`, { as: MEMBER.email, method: "PATCH", body: { capital: 6000 } }));
    expect(cap.capital).toBe(6000);
    expect((await call(`/api/profiles/${memberProfile}`, { as: MEMBER.email, method: "PATCH", body: {} })).status).toBe(422);
    expect((await call(`/api/profiles/${memberProfile}`, { as: MEMBER.email, method: "PATCH", body: { capital: null } })).status).toBe(422);
    expect((await call(`/api/profiles/no-es-uuid`, { as: MEMBER.email })).status).toBe(422);
  });

  it("borra el propio perfil en cascada", async () => {
    await env.DB.prepare("INSERT OR IGNORE INTO users (id, email) VALUES (?, ?)").bind("eeeeeeee-0000-0000-0000-000000000005", "borra@test.dev").run();
    const id = await ensureProfile("borra@test.dev");
    expect((await call(`/api/profiles/${id}`, { as: "borra@test.dev", method: "DELETE" })).status).toBe(204);
    expect((await call(`/api/profiles/${id}`, { as: "borra@test.dev" })).status).toBe(404);
  });
});

describe("metas", () => {
  let goalId: string;

  it("crea, lista, edita y borra", async () => {
    const created = await call("/api/goals", {
      as: MEMBER.email,
      method: "POST",
      body: { profile_id: memberProfile, target_return: "0.15", deadline: day(365) },
    });
    expect(created.status).toBe(201);
    goalId = (await json(created)).id;
    const list = await json(await call(`/api/goals?profile_id=${memberProfile}`, { as: MEMBER.email }));
    expect(list.some((g: { id: string }) => g.id === goalId)).toBe(true);
    expect((await json(await call("/api/goals", { as: MEMBER.email }))).length).toBeGreaterThan(0);
    const patched = await json(await call(`/api/goals/${goalId}`, { as: MEMBER.email, method: "PATCH", body: { target_return: 0.2, deadline: day(400) } }));
    expect(patched).toMatchObject({ target_return: 0.2, deadline: day(400) });
    expect((await call(`/api/goals/${goalId}`, { as: MEMBER.email })).status).toBe(200);
  });

  it("422 con fecha pasada o retorno <= 0", async () => {
    const past = await call("/api/goals", { as: MEMBER.email, method: "POST", body: { profile_id: memberProfile, target_return: 0.1, deadline: "2020-01-01" } });
    expect(past.status).toBe(422);
    const zero = await call("/api/goals", { as: MEMBER.email, method: "POST", body: { profile_id: memberProfile, target_return: 0, deadline: day(30) } });
    expect(zero.status).toBe(422);
    const badDate = await call("/api/goals", { as: MEMBER.email, method: "POST", body: { profile_id: memberProfile, target_return: 0.1, deadline: "2030-02-31" } });
    expect(badDate.status).toBe(422);
  });

  it("404 al crear en un perfil ajeno o leer una meta ajena", async () => {
    const res = await call("/api/goals", { as: MEMBER.email, method: "POST", body: { profile_id: otherProfile, target_return: 0.1, deadline: day(30) } });
    expect(res.status).toBe(404);
    expect((await call(`/api/goals/${goalId}`, { as: OTHER.email })).status).toBe(404);
    expect((await call(`/api/goals/${goalId}`, { as: OTHER.email, method: "DELETE" })).status).toBe(404);
  });

  it("probabilidad de la meta", async () => {
    const res = await call(`/api/goals/${goalId}/probability`, { as: MEMBER.email });
    expect(res.status).toBe(200);
    const p = await json(res);
    expect(p).toMatchObject({ goal_id: goalId, profile_id: memberProfile, capital: 6000 });
    expect(p.probability).toBeGreaterThanOrEqual(0);
    expect(p.probability).toBeLessThanOrEqual(1);
    expect(Array.isArray(p.fan)).toBe(true);
  });

  it("422 si la fecha límite ya pasó", async () => {
    const id = crypto.randomUUID();
    await env.DB.prepare("INSERT INTO goals (id, profile_id, target_return, deadline) VALUES (?, ?, 0.1, ?)").bind(id, memberProfile, day(-3)).run();
    const res = await call(`/api/goals/${id}/probability`, { as: MEMBER.email });
    expect(res.status).toBe(422);
    expect((await json(res)).detail).toContain("ya pasó");
  });

  it("borra la meta", async () => {
    expect((await call(`/api/goals/${goalId}`, { as: MEMBER.email, method: "DELETE" })).status).toBe(204);
    expect((await call(`/api/goals/${goalId}`, { as: MEMBER.email })).status).toBe(404);
  });
});

describe("bots", () => {
  let botId: string;

  it("crea detenido en el perfil del usuario", async () => {
    const res = await call("/api/bots", { as: MEMBER.email, method: "POST", body: { name: "  Mi bot ", strategy_config: { trend: { enabled: true } } } });
    expect(res.status).toBe(201);
    const bot = await json(res);
    botId = bot.id;
    expect(bot).toMatchObject({ name: "Mi bot", status: "stopped", profile_id: memberProfile, strategy_config: { trend: { enabled: true } } });
  });

  it("422 con nombre vacío", async () => {
    expect((await call("/api/bots", { as: MEMBER.email, method: "POST", body: { name: "   " } })).status).toBe(422);
  });

  it("arranca, detiene y encola los comandos", async () => {
    const start = await json(await call(`/api/bots/${botId}/start`, { as: MEMBER.email, method: "POST" }));
    expect(start).toMatchObject({ command: "start", bot: { status: "running" } });
    expect(start.bot.started_at).toBeTruthy();
    expect((await call(`/api/bots/${botId}/start`, { as: MEMBER.email, method: "POST" })).status).toBe(409);
    expect((await call(`/api/bots/${botId}`, { as: MEMBER.email, method: "DELETE" })).status).toBe(409);
    const stop = await json(await call(`/api/bots/${botId}/stop`, { as: MEMBER.email, method: "POST" }));
    expect(stop).toMatchObject({ command: "stop", bot: { status: "stopped" } });
    expect((await call(`/api/bots/${botId}/stop`, { as: MEMBER.email, method: "POST" })).status).toBe(409);
    const cmd = await env.DB.prepare("SELECT type, payload FROM commands WHERE id = ?").bind(start.command_id).first<{ type: string; payload: string }>();
    expect(cmd?.type).toBe("start");
    expect(JSON.parse(cmd!.payload)).toEqual({ strategy_config: { trend: { enabled: true } } });
  });

  it("kill con motivo y kill-all", async () => {
    const kill = await json(await call(`/api/bots/${botId}/kill`, { as: MEMBER.email, method: "POST", body: { reason: "prueba" } }));
    expect(kill).toMatchObject({ command: "kill", bot: { status: "killed" } });
    const cmd = await env.DB.prepare("SELECT reason, payload FROM commands WHERE id = ?").bind(kill.command_id).first<{ reason: string; payload: string }>();
    expect(cmd).toMatchObject({ reason: "prueba", payload: '{"close_positions":true}' });
    await call("/api/bots", { as: MEMBER.email, method: "POST", body: { name: "Otro" } });
    const all = await json(await call("/api/bots/kill-all", { as: MEMBER.email, method: "POST" }));
    expect(all.killed.length).toBe(1);
    expect(all.killed[0].bot.status).toBe("killed");
  });

  it("filtra, edita y borra", async () => {
    const killed = await json(await call("/api/bots?status=killed", { as: MEMBER.email }));
    expect(killed.length).toBe(2);
    expect((await call("/api/bots?status=otro", { as: MEMBER.email })).status).toBe(422);
    const byProfile = await json(await call(`/api/bots?profile_id=${memberProfile}`, { as: MEMBER.email }));
    expect(byProfile.length).toBe(2);
    const patched = await json(await call(`/api/bots/${botId}`, { as: MEMBER.email, method: "PATCH", body: { name: "Renombrado" } }));
    expect(patched.name).toBe("Renombrado");
    const cfg = await json(await call(`/api/bots/${botId}`, { as: MEMBER.email, method: "PATCH", body: { strategy_config: { x: 1 } } }));
    expect(cfg.strategy_config).toEqual({ x: 1 });
    expect((await call(`/api/bots/${botId}`, { as: MEMBER.email, method: "PATCH", body: {} })).status).toBe(422);
    expect((await call(`/api/bots/${botId}`, { as: OTHER.email })).status).toBe(404);
    expect((await call(`/api/bots/${botId}/start`, { as: OTHER.email, method: "POST" })).status).toBe(404);
    expect((await call(`/api/bots/${botId}`, { as: MEMBER.email })).status).toBe(200);
  });
});

describe("portafolio y trades", () => {
  let botId: string;

  beforeAll(async () => {
    const res = await call("/api/bots", { as: OTHER.email, method: "POST", body: { name: "Sleeve" } });
    botId = (await json(res)).id;
    const db = env.DB;
    await db.batch([
      db
        .prepare(
          "INSERT INTO trades (id, bot_id, symbol, side, quantity, entry_price, exit_price, fees, pnl, rationale, opened_at, closed_at) VALUES (?, ?, 'BTCUSDT', 'buy', 0.1, 60000, 61000, 1, 99, '{\"sleeve\":\"trend\"}', ?, ?)",
        )
        .bind(crypto.randomUUID(), botId, iso(-3), iso(-2)),
      db
        .prepare("INSERT INTO trades (id, bot_id, symbol, side, quantity, entry_price, fees, rationale, opened_at) VALUES (?, ?, 'ETHUSDT', 'buy', 1, 3000, 1, '{}', ?)")
        .bind(crypto.randomUUID(), botId, iso(-1)),
      db.prepare("INSERT INTO equity_snapshots (bot_id, ts, equity, cash, positions_value) VALUES (?, ?, 20000, 20000, 0)").bind(botId, iso(-2)),
      db.prepare("INSERT INTO equity_snapshots (bot_id, ts, equity, cash, positions_value) VALUES (?, ?, 20100, 17100, 3000)").bind(botId, iso(-1)),
    ]);
  });

  it("suma los sleeves con la última foto", async () => {
    const p = await json(await call("/api/portfolio", { as: OTHER.email }));
    expect(p).toMatchObject({ capital: 20000, equity: 20100, cash: 17100, positions_value: 3000, realized_pnl: 99 });
    expect(p.total_return).toBeCloseTo(0.005);
    expect(p.sleeves[0]).toMatchObject({ bot_id: botId, open_trades: 1, realized_pnl: 99 });
    expect(p.open_positions).toHaveLength(1);
  });

  it("sin fotos vale el capital", async () => {
    const p = await json(await call("/api/portfolio", { as: MEMBER.email }));
    expect(p).toMatchObject({ equity: 6000, cash: 6000, positions_value: 0, total_return: 0 });
  });

  it("trades paginados solo del usuario", async () => {
    const page = await json(await call("/api/trades?limit=1", { as: OTHER.email }));
    expect(page).toMatchObject({ total: 2, limit: 1, offset: 0 });
    expect(page.items[0].symbol).toBe("ETHUSDT");
    expect((await json(await call("/api/trades?state=closed", { as: OTHER.email }))).total).toBe(1);
    expect((await json(await call("/api/trades?state=open&symbol=ETHUSDT", { as: OTHER.email }))).total).toBe(1);
    expect((await json(await call(`/api/trades?bot_id=${botId}`, { as: MEMBER.email }))).total).toBe(0);
    expect((await call("/api/trades?limit=500", { as: OTHER.email })).status).toBe(422);
  });

  it("dashboard con los datos del usuario", async () => {
    const d = await json(await call("/api/dashboard", { as: OTHER.email }));
    expect(d.profile).toMatchObject({ capital: 20000, risk_level: 4 });
    expect(d.bots).toHaveLength(1);
    expect(d.snapshots.map((s: { equity: number }) => s.equity)).toEqual([20000, 20100]);
    expect(d.openTrades).toHaveLength(1);
    expect(Object.values(d.closed)).toEqual([{ sleeve: "trend", pnl: 99 }]);
    const snaps = await json(await call(`/api/equity-snapshots?bot_id=${botId}&since=${encodeURIComponent(iso(-1.5))}`, { as: OTHER.email }));
    expect(snaps).toHaveLength(1);
    expect(await json(await call("/api/equity-snapshots", { as: MEMBER.email }))).toEqual([]);
  });

  it("dashboard vacío sin perfil o sin bots", async () => {
    const d = await json(await call("/api/dashboard", { as: "sinperfil@test.dev" }));
    expect(d.profile).toBeNull();
  });
});

describe("backtests", () => {
  it("encola, lista y lee", async () => {
    const res = await call("/api/backtests", {
      as: MEMBER.email,
      method: "POST",
      body: { config: { symbols: ["BTCUSDT"], start: "2021-01-01", end: "2024-12-31", strategies: ["trend"] } },
    });
    expect(res.status).toBe(202);
    const bt = await json(res);
    expect(bt).toMatchObject({ status: "queued", profile_id: memberProfile });
    expect(bt.config).toMatchObject({ timeframe: "15m", mode: "walk_forward" });
    expect(bt.config.initial_capital).toBeUndefined();
    expect((await json(await call("/api/backtests?status=queued", { as: MEMBER.email }))).length).toBe(1);
    expect((await call(`/api/backtests/${bt.id}`, { as: MEMBER.email })).status).toBe(200);
    expect((await call(`/api/backtests/${bt.id}`, { as: OTHER.email })).status).toBe(404);
  });

  it("422 con símbolos repetidos o fechas al revés", async () => {
    const dup = await call("/api/backtests", { as: MEMBER.email, method: "POST", body: { config: { symbols: ["BTCUSDT", "BTCUSDT"], start: "2021-01-01", end: "2022-01-01" } } });
    expect(dup.status).toBe(422);
    const dates = await call("/api/backtests", { as: MEMBER.email, method: "POST", body: { config: { symbols: ["ETHUSDT"], start: "2022-01-01", end: "2021-01-01" } } });
    expect(dates.status).toBe(422);
  });
});

describe("onboarding", () => {
  const EMAIL = "onboarding@test.dev";

  beforeAll(async () => {
    await env.DB.prepare("INSERT OR IGNORE INTO users (id, email) VALUES (?, ?)").bind("ffffffff-0000-0000-0000-000000000006", EMAIL).run();
  });

  it("null sin perfil; PUT crea y luego reemplaza metas", async () => {
    expect(await json(await call("/api/onboarding", { as: EMAIL }))).toBeNull();
    const first = await json(
      await call("/api/onboarding", {
        as: EMAIL,
        method: "PUT",
        body: { risk_level: 4, capital: 2500, goals: [{ target_return: 0.1, deadline: day(100) }, { target_return: 0.3, deadline: day(700) }] },
      }),
    );
    const again = await json(
      await call("/api/onboarding", { as: EMAIL, method: "PUT", body: { risk_level: 2, capital: 3000, goals: [{ target_return: 0.05, deadline: day(50) }] } }),
    );
    expect(again.profileId).toBe(first.profileId);
    const row = await json(await call("/api/onboarding", { as: EMAIL }));
    expect(row).toMatchObject({ risk_level: 2, capital: 3000, max_drawdown: 0.15 });
    expect(row.goals).toEqual([{ target_return: 0.05, deadline: day(50) }]);
  });
});
