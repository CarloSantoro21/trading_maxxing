import { describe, expect, it } from "vitest";
import {
  EMPTY_DASHBOARD,
  NO_SLEEVE,
  portfolioEquity,
  sleeveOf,
  sleeves,
  summarize,
  type BotRow,
  type DashboardData,
  type TradeRow,
  withBot,
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

describe("withBot", () => {
  it("actualiza un bot existente y agrega uno nuevo del mismo perfil", () => {
    let state = withBot(base, { ...bot, status: "stopped" });
    expect(state.bots[0].status).toBe("stopped");
    state = withBot(state, { ...bot, id: "b2" });
    expect(state.bots.map((b) => b.id)).toEqual(["b1", "b2"]);
  });

  it("ignora bots de otro perfil o si no hay perfil", () => {
    expect(withBot(base, { ...bot, id: "b2", profile_id: "p2" })).toBe(base);
    expect(withBot(EMPTY_DASHBOARD, bot)).toBe(EMPTY_DASHBOARD);
  });
});
