/** Endpoints /internal/* que usa el motor de la Mac. */

import { env } from "cloudflare:test";
import { beforeAll, describe, expect, it } from "vitest";
import { MEMBER, call, day, ensureProfile, iso, json, seedBase, service } from "./helpers";

let botId: string;
let strategyId: string;

beforeAll(async () => {
  await seedBase();
  await ensureProfile(MEMBER.email, 8000, 3);
  const bot = await json(await call("/api/bots", { as: MEMBER.email, method: "POST", body: { name: "Motor" } }));
  botId = bot.id;
  await call(`/api/bots/${botId}/start`, { as: MEMBER.email, method: "POST" });
});

describe("registro de estrategias", () => {
  it("crea, lee, actualiza y lista por estado", async () => {
    const created = await service("/internal/strategies", { method: "POST", body: { name: "motor_x", params: { a: 1 } } });
    expect(created.status).toBe(201);
    const s = await json(created);
    strategyId = s.id;
    expect(s).toMatchObject({ name: "motor_x", state: "research", params: { a: 1 } });
    const dup = await service("/internal/strategies", { method: "POST", body: { name: "motor_x" } });
    expect(dup.status).toBe(409);
    expect((await json(dup)).code).toBe("strategy_exists");
    expect((await service("/internal/strategies", { method: "POST", body: { name: "Mal" } })).status).toBe(422);
    const patched = await json(
      await service("/internal/strategies/motor_x", { method: "PATCH", body: { lab_report: { sharpe_oos: 1 }, live_metrics: { trades: 3 } } }),
    );
    expect(patched).toMatchObject({ lab_report: { sharpe_oos: 1 }, live_metrics: { trades: 3 } });
    expect((await service("/internal/strategies/nope", { method: "PATCH", body: { params: {} } })).status).toBe(404);
    expect((await json(await service("/internal/strategies/motor_x"))).name).toBe("motor_x");
    expect((await service("/internal/strategies/nope")).status).toBe(404);
    const research = await json(await service("/internal/strategies?state=research,lab"));
    expect(research.some((r: { name: string }) => r.name === "motor_x")).toBe(true);
    expect((await service("/internal/strategies?state=otro")).status).toBe(422);
  });

  it("transiciones con validación y detección de estado viejo", async () => {
    const t = await service("/internal/strategies/motor_x/transitions", {
      method: "POST",
      body: { from_state: "research", to_state: "lab", actor: "system", reason: "entra" },
    });
    expect(t.status).toBe(201);
    expect(await json(t)).toMatchObject({ strategy: "motor_x", from_state: "research", to_state: "lab", actor: "system" });
    const stale = await service("/internal/strategies/motor_x/transitions", {
      method: "POST",
      body: { from_state: "research", to_state: "lab", actor: "system", reason: "otra vez" },
    });
    expect(stale.status).toBe(409);
    expect((await json(stale)).code).toBe("stale_state");
    const notAllowed = await service("/internal/strategies/motor_x/transitions", {
      method: "POST",
      body: { from_state: "lab", to_state: "active", actor: "system", reason: "x" },
    });
    expect((await json(notAllowed)).code).toBe("transition_not_allowed");
    await service("/internal/strategies/motor_x/transitions", { method: "POST", body: { from_state: "lab", to_state: "shadow", actor: "system", reason: "ok" } });
    const human = await service("/internal/strategies/motor_x/transitions", {
      method: "POST",
      body: { from_state: "shadow", to_state: "active", actor: "system", reason: "x" },
    });
    expect((await json(human)).code).toBe("transition_not_allowed");
    const hist = await json(await service("/internal/strategies/motor_x/transitions"));
    expect(hist.map((h: { to_state: string }) => h.to_state)).toEqual(["lab", "shadow"]);
    expect((await service("/internal/strategies/nope/transitions")).status).toBe(404);
  });

  it("el trigger rechaza una transición con estado viejo aunque se salte la API", async () => {
    await expect(
      env.DB.prepare("INSERT INTO strategy_transitions (strategy_id, from_state, to_state, actor, reason) VALUES (?, 'research', 'lab', 'system', 'x')")
        .bind(strategyId)
        .run(),
    ).rejects.toThrow(/stale_state/);
  });
});

describe("bots, trades y equity", () => {
  let tradeId: string;

  it("bots en running con su perfil", async () => {
    const bots = await json(await service("/internal/bots?status=running"));
    const bot = bots.find((b: { id: string }) => b.id === botId);
    expect(bot.profile).toMatchObject({ risk_level: 3, capital: 8000, max_drawdown: 0.2, horizon: "medium", instruments: ["BTCUSDT", "ETHUSDT"] });
    expect((await json(await service(`/internal/bots/${botId}`))).id).toBe(botId);
    expect((await service(`/internal/bots/${crypto.randomUUID()}`)).status).toBe(404);
    expect((await json(await service("/internal/bots"))).length).toBeGreaterThan(0);
  });

  it("abre, actualiza y cierra un trade", async () => {
    const res = await service("/internal/trades", {
      method: "POST",
      body: {
        bot_id: botId,
        symbol: "BTCUSDT",
        side: "buy",
        quantity: "0.01",
        entry_price: "60000",
        stop_loss: "59000",
        take_profit: "62000",
        fees: "0.5",
        rationale: { sleeve: "trend" },
        opened_at: "2026-11-20T10:00:00.123456+00:00",
      },
    });
    expect(res.status).toBe(201);
    const trade = await json(res);
    tradeId = trade.id;
    expect(trade).toMatchObject({ quantity: 0.01, opened_at: "2026-11-20T10:00:00.123Z", rationale: { sleeve: "trend" } });
    const open = await json(await service(`/internal/trades?bot_id=${botId}&open=true&side=buy`));
    expect(open).toHaveLength(1);
    const closed = await json(
      await service(`/internal/trades/${tradeId}`, {
        method: "PATCH",
        body: { exit_price: "61000", closed_at: "2026-11-20T12:00:00Z", pnl: "9.5", rationale: { sleeve: "trend", position: {} } },
      }),
    );
    expect(closed).toMatchObject({ exit_price: 61000, pnl: 9.5, closed_at: "2026-11-20T12:00:00.000Z" });
    expect((await json(await service(`/internal/trades?bot_id=${botId}&open=false`))).length).toBe(1);
    expect((await service(`/internal/trades/${crypto.randomUUID()}`, { method: "PATCH", body: { fees: 1 } })).status).toBe(404);
  });

  it("422 si el trade rompe una regla de la base", async () => {
    const bad = await service("/internal/trades", {
      method: "POST",
      body: { bot_id: botId, symbol: "BTCUSDT", side: "buy", quantity: 1, entry_price: 100, stop_loss: 200 },
    });
    expect(bad.status).toBe(422);
    expect((await json(bad)).code).toBe("check_violation");
    const fk = await service("/internal/trades", {
      method: "POST",
      body: { bot_id: crypto.randomUUID(), symbol: "BTCUSDT", side: "buy", quantity: 1, entry_price: 100 },
    });
    expect((await json(fk)).code).toBe("foreign_key_violation");
  });

  it("equity snapshots con upsert", async () => {
    const body = { snapshots: [{ bot_id: botId, ts: "2026-11-20T00:00:00Z", equity: 8000, cash: 8000 }] };
    expect(await json(await service("/internal/equity-snapshots", { method: "POST", body }))).toEqual({ upserted: 1 });
    body.snapshots[0]!.equity = 8100;
    await service("/internal/equity-snapshots", { method: "POST", body });
    const rows = await env.DB.prepare("SELECT equity FROM equity_snapshots WHERE bot_id = ?").bind(botId).all<{ equity: number }>();
    expect(rows.results.map((r) => r.equity)).toEqual([8100]);
  });
});

describe("ledger de referencia", () => {
  it("trades idempotentes y equity con upsert", async () => {
    const id = (await json(await service("/internal/strategies/motor_x"))).id;
    const trade = {
      strategy_id: id,
      started_at: iso(-10),
      state: "shadow",
      symbol: "ETHUSDT",
      quantity: 1,
      entry_price: 3000,
      exit_price: 3100,
      pnl: 100,
      ret: 0.033,
      opened_at: iso(-5),
      closed_at: iso(-4),
    };
    expect(await json(await service("/internal/reference/trades", { method: "POST", body: { rows: [trade] } }))).toEqual({ received: 1, inserted: 1 });
    expect(await json(await service("/internal/reference/trades", { method: "POST", body: { rows: [trade] } }))).toEqual({ received: 1, inserted: 0 });
    expect(await json(await service("/internal/reference/trades", { method: "POST", body: { rows: [] } }))).toEqual({ received: 0, inserted: 0 });
    const point = { strategy_id: id, started_at: iso(-10), day: day(-2), state: "shadow", equity: 1000, cash: 1000 };
    await service("/internal/reference/equity", { method: "PUT", body: { points: [point] } });
    await service("/internal/reference/equity", { method: "PUT", body: { points: [{ ...point, equity: 1010 }] } });
    expect(await json(await service("/internal/reference/equity", { method: "PUT", body: { points: [] } }))).toEqual({ upserted: 0 });
    const rows = await json(await service(`/internal/reference/equity?strategy_id=${id}`));
    expect(rows).toHaveLength(1);
    expect(rows[0].equity).toBe(1010);
  });
});

describe("comandos, backtests, decisiones, estado y latido", () => {
  it("lee los comandos pendientes y los confirma", async () => {
    const pending = await json(await service("/internal/commands"));
    const mine = pending.filter((c: { bot_id: string }) => c.bot_id === botId);
    expect(mine[0]).toMatchObject({ type: "start", payload: { strategy_config: {} } });
    const ack = await json(await service(`/internal/commands/${mine[0].id}/ack`, { method: "POST" }));
    expect(ack).toMatchObject({ ack_status: "done" });
    const after = await json(await service(`/internal/commands?after=0`));
    expect(after.some((c: { id: string }) => c.id === mine[0].id)).toBe(false);
    const failed = await json(await call(`/api/bots/${botId}/stop`, { as: MEMBER.email, method: "POST" }));
    const nack = await json(await service(`/internal/commands/${failed.command_id}/ack`, { method: "POST", body: { status: "failed", error: "sin red" } }));
    expect(nack).toMatchObject({ ack_status: "failed", ack_error: "sin red" });
    expect((await service(`/internal/commands/${crypto.randomUUID()}/ack`, { method: "POST" })).status).toBe(404);
  });

  it("toma un backtest en cola y escribe el resultado", async () => {
    const bt = await json(
      await call("/api/backtests", { as: MEMBER.email, method: "POST", body: { config: { symbols: ["BTCUSDT"], start: "2021-01-01", end: "2022-01-01" } } }),
    );
    const queued = await json(await service("/internal/backtests?status=queued"));
    expect(queued.find((b: { id: string }) => b.id === bt.id).user_id).toBe(MEMBER.id);
    expect((await json(await service("/internal/backtests"))).length).toBeGreaterThan(0);
    await service(`/internal/backtests/${bt.id}`, { method: "PATCH", body: { status: "running", started_at: iso() } });
    const bad = await service(`/internal/backtests/${bt.id}`, { method: "PATCH", body: { status: "done" } });
    expect(bad.status).toBe(422);
    const done = await json(await service(`/internal/backtests/${bt.id}`, { method: "PATCH", body: { status: "done", report: { sharpe: 1 }, finished_at: iso(1) } }));
    expect(done).toMatchObject({ status: "done", report: { sharpe: 1 } });
    expect((await service(`/internal/backtests/${crypto.randomUUID()}`, { method: "PATCH", body: { status: "running" } })).status).toBe(404);
  });

  it("decisiones visibles para el dueño del bot", async () => {
    const res = await service("/internal/decisions", {
      method: "POST",
      body: { decisions: [{ bot_id: botId, strategy: "trend", symbol: "BTCUSDT", kind: "trade_decision", payload: { side: "buy" } }] },
    });
    expect(res.status).toBe(201);
    const mine = await json(await call(`/api/decisions?bot_id=${botId}`, { as: MEMBER.email }));
    expect(mine[0]).toMatchObject({ kind: "trade_decision", payload: { side: "buy" } });
    expect(await json(await call("/api/decisions", { as: "other@test.dev" }))).toEqual([]);
  });

  it("estado del orquestador", async () => {
    expect((await service("/internal/state/peaks")).status).toBe(404);
    await service("/internal/state/peaks", { method: "PUT", body: { value: { bot: 1 } } });
    expect((await json(await service("/internal/state/peaks"))).value).toEqual({ bot: 1 });
  });

  it("latido y estado del motor", async () => {
    const off = await json(await call("/api/engine/status", { as: MEMBER.email }));
    expect(off.online).toBe(false);
    const hb = await json(await service("/internal/heartbeat", { method: "POST", body: { component: "agents", version: "1.0", details: { bots: 1 } } }));
    expect(hb).toMatchObject({ component: "agents", status: "ok", details: { bots: 1 } });
    const on = await json(await call("/api/engine/status", { as: MEMBER.email }));
    expect(on.online).toBe(true);
    expect(on.components[0]).toMatchObject({ component: "agents", online: true });
    await env.DB.prepare("UPDATE engine_heartbeats SET last_seen_at = ? WHERE component = 'agents'").bind(iso(-1)).run();
    expect((await json(await call("/api/engine/status", { as: MEMBER.email }))).online).toBe(false);
    expect((await service("/internal/heartbeat", { method: "POST", body: { component: "Mal Nombre" } })).status).toBe(422);
  });
});
