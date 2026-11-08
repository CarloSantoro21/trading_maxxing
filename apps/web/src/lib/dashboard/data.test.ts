import { describe, expect, it } from "vitest";
import { called, createFakeSupabase } from "@/test/fakeSupabase";
import {
  dashboardReducer,
  DashboardLoadError,
  EMPTY_DASHBOARD,
  fetchDashboard,
  MAX_SNAPSHOTS,
  NO_SLEEVE,
  portfolioEquity,
  sleeveOf,
  sleeves,
  summarize,
  type BotRow,
  type DashboardData,
  type TradeRow,
} from "./data";

const profile = { id: "p1", capital: 1000, risk_level: 3, max_drawdown: 0.2 };
const bot: BotRow = { id: "b1", profile_id: "p1", name: "Mi bot", status: "running", started_at: null, stopped_at: null };

function trade(over: Partial<TradeRow> = {}): TradeRow {
  return {
    id: "t1",
    bot_id: "b1",
    symbol: "BTCUSDT",
    side: "buy",
    quantity: "0.01",
    entry_price: "50000",
    exit_price: null,
    stop_loss: "48000",
    take_profit: "55000",
    fees: "0.5",
    pnl: null,
    rationale: { sleeve: "trend" },
    opened_at: "2026-11-01T10:00:00Z",
    closed_at: null,
    ...over,
  };
}

const base: DashboardData = { ...EMPTY_DASHBOARD, profile, bots: [bot] };

describe("fetchDashboard", () => {
  it("sin perfil devuelve el estado vacío", async () => {
    const fake = createFakeSupabase(() => ({ data: null }));
    await expect(fetchDashboard(fake.client, "u1")).resolves.toEqual(EMPTY_DASHBOARD);
  });

  it("falla con un mensaje claro si no puede leer el perfil", async () => {
    const fake = createFakeSupabase(() => ({ error: { message: "boom" } }));
    const err = await fetchDashboard(fake.client, "u1").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(DashboardLoadError);
    expect((err as Error).message).toBe("No se pudo leer tu perfil.");
  });

  it("sin bots no consulta trades ni equity", async () => {
    const fake = createFakeSupabase((q) =>
      q.table === "profiles" ? { data: { ...profile, capital: "1000" } } : { data: [] },
    );
    const data = await fetchDashboard(fake.client, "u1");
    expect(data.profile).toEqual(profile);
    expect(data.bots).toEqual([]);
    expect(fake.queries.map((q) => q.table)).toEqual(["profiles", "bots"]);
  });

  it("lee bots, curva, operaciones y P&L por sleeve", async () => {
    const fake = createFakeSupabase((q) => {
      if (q.table === "profiles") return { data: profile };
      if (q.table === "bots") return { data: [bot] };
      if (q.table === "equity_snapshots")
        return {
          data: [
            { bot_id: "b1", ts: "2026-11-02T00:00:00Z", equity: "1010" },
            { bot_id: "b1", ts: "2026-11-01T00:00:00Z", equity: "1000" },
          ],
        };
      if (called(q, "not")) return { data: [{ id: "t0", pnl: "12.5", sleeve: "trend" }, { id: "t9", pnl: null, sleeve: null }] };
      if (called(q, "is")) return { data: [trade()] };
      return { data: [trade(), trade({ id: "t0", closed_at: "2026-11-01T12:00:00Z" })] };
    });
    const data = await fetchDashboard(fake.client, "u1");
    expect(data.snapshots.map((s) => s.ts)).toEqual(["2026-11-01T00:00:00Z", "2026-11-02T00:00:00Z"]);
    expect(data.recentTrades).toHaveLength(2);
    expect(data.openTrades).toHaveLength(1);
    expect(data.closed).toEqual({ t0: { sleeve: "trend", pnl: 12.5 }, t9: { sleeve: NO_SLEEVE, pnl: 0 } });
  });

  it("propaga el error de una consulta secundaria", async () => {
    const fake = createFakeSupabase((q) => {
      if (q.table === "profiles") return { data: profile };
      if (q.table === "bots") return { data: [bot] };
      if (q.table === "equity_snapshots") return { error: { message: "x" } };
      return { data: [] };
    });
    await expect(fetchDashboard(fake.client, "u1")).rejects.toThrow("No se pudo leer tu curva de equity.");
  });
});

describe("cálculos", () => {
  it("portfolioEquity suma la última foto de cada bot e ignora puntos inválidos", () => {
    const points = portfolioEquity([
      { bot_id: "b1", ts: "2026-11-01T00:00:00Z", equity: 100 },
      { bot_id: "b2", ts: "2026-11-01T00:00:00Z", equity: 50 },
      { bot_id: "b1", ts: "2026-11-02T00:00:00Z", equity: "110" },
      { bot_id: "b1", ts: "no-es-fecha", equity: 1 },
      { bot_id: "b1", ts: "2026-11-03T00:00:00Z", equity: "nada" },
    ]);
    expect(points.map((p) => p.equity)).toEqual([150, 160]);
  });

  it("summarize calcula P&L, retorno y caída desde el máximo", () => {
    const data: DashboardData = {
      ...base,
      snapshots: [
        { bot_id: "b1", ts: "2026-11-01T00:00:00Z", equity: 1200 },
        { bot_id: "b1", ts: "2026-11-02T00:00:00Z", equity: 1080 },
      ],
      closed: { t0: { sleeve: "trend", pnl: 30 }, t1: { sleeve: "trend", pnl: -10 } },
    };
    const s = summarize(data);
    expect(s.equity).toBe(1080);
    expect(s.pnl).toBe(80);
    expect(s.totalReturn).toBeCloseTo(0.08);
    expect(s.realizedPnl).toBe(20);
    expect(s.drawdown).toBeCloseTo(0.1);
    expect(s.asOf).toBe("2026-11-02T00:00:00Z");
  });

  it("summarize sin datos usa el capital", () => {
    expect(summarize(EMPTY_DASHBOARD)).toMatchObject({ capital: 0, equity: 0, totalReturn: 0, drawdown: 0, asOf: null });
  });

  it("sleeves agrupa posiciones abiertas y P&L realizado", () => {
    const result = sleeves({
      ...base,
      openTrades: [trade(), trade({ id: "t2", rationale: null, quantity: 1, entry_price: 10 })],
      closed: { t0: { sleeve: "trend", pnl: 5 } },
    });
    expect(result.map((s) => s.sleeve)).toEqual([NO_SLEEVE, "trend"]);
    const trend = result[1];
    expect(trend.invested).toBeCloseTo(500);
    expect(trend.realizedPnl).toBe(5);
    expect(trend.closedTrades).toBe(1);
    expect(trend.positions[0]).toMatchObject({ stopLoss: 48000, takeProfit: 55000 });
  });

  it("sleeveOf cae en sin_sleeve si el rationale no lo trae", () => {
    expect(sleeveOf({ rationale: { sleeve: "" } })).toBe(NO_SLEEVE);
    expect(sleeveOf({ rationale: [] })).toBe(NO_SLEEVE);
  });
});

describe("dashboardReducer", () => {
  it("agrega una operación nueva y la quita de abiertas al cerrarse", () => {
    let state = dashboardReducer(base, { type: "trade", row: trade() });
    expect(state.recentTrades).toHaveLength(1);
    expect(state.openTrades).toHaveLength(1);
    state = dashboardReducer(state, {
      type: "trade",
      row: trade({ closed_at: "2026-11-02T00:00:00Z", pnl: "7", exit_price: "51000" }),
    });
    expect(state.recentTrades).toHaveLength(1);
    expect(state.openTrades).toHaveLength(0);
    expect(state.closed.t1).toEqual({ sleeve: "trend", pnl: 7 });
  });

  it("ignora eventos de bots ajenos", () => {
    expect(dashboardReducer(base, { type: "trade", row: trade({ bot_id: "otro" }) })).toBe(base);
    const snap = { bot_id: "otro", ts: "2026-11-01T00:00:00Z", equity: 1 };
    expect(dashboardReducer(base, { type: "snapshot", row: snap })).toBe(base);
    expect(dashboardReducer(base, { type: "bot", row: { ...bot, id: "b2", profile_id: "p2" } })).toBe(base);
  });

  it("agrega fotos de equity sin duplicar y respeta el tope", () => {
    const snap = { bot_id: "b1", ts: "2026-11-01T00:00:00Z", equity: 1 };
    const once = dashboardReducer(base, { type: "snapshot", row: snap });
    expect(dashboardReducer(once, { type: "snapshot", row: snap })).toBe(once);
    const full: DashboardData = {
      ...base,
      snapshots: Array.from({ length: MAX_SNAPSHOTS }, (_, i) => ({ bot_id: "b1", ts: `t${i}`, equity: i })),
    };
    expect(dashboardReducer(full, { type: "snapshot", row: snap }).snapshots).toHaveLength(MAX_SNAPSHOTS);
  });

  it("actualiza, agrega y borra bots", () => {
    let state = dashboardReducer(base, { type: "bot", row: { ...bot, status: "stopped" } });
    expect(state.bots[0].status).toBe("stopped");
    state = dashboardReducer(state, { type: "bot", row: { ...bot, id: "b2" } });
    expect(state.bots).toHaveLength(2);
    state = dashboardReducer(state, { type: "botDeleted", id: "b1" });
    expect(state.bots.map((b) => b.id)).toEqual(["b2"]);
    expect(dashboardReducer(EMPTY_DASHBOARD, { type: "loaded", data: base })).toBe(base);
  });
});
