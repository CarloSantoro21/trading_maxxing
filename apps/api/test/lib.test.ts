/** Pruebas unitarias de los ports numéricos: Monte Carlo, exposición, allocator y proyección. */

import { describe, expect, it } from "vitest";
import { allocate } from "../src/lib/allocator";
import { exposureForLevel, sensitivityFor, TREND_SENSITIVITY } from "../src/lib/exposure";
import { detail, drawdownHistogram, returnsFromEquity, summary } from "../src/lib/lab-core";
import { bootstrap, histogram, mulberry32, percentile, stdSample } from "../src/lib/montecarlo";
import { ProjectionError, effectiveReport, fanIndices, projectGoal, simulationsFor, type StrategySeries } from "../src/lib/projection";
import { levelDrawdown } from "../src/lib/risk";
import { isAllowedTransition, shadowDaysClean } from "../src/lib/strategies";

function series(n: number, drift = 0.001, start = "2024-01-01"): [string, number][] {
  const rng = mulberry32(7);
  const base = Date.parse(`${start}T00:00:00Z`);
  return Array.from({ length: n }, (_, i) => [new Date(base + i * 86_400_000).toISOString().slice(0, 10), drift + (rng() - 0.5) * 0.02]);
}

describe("montecarlo", () => {
  it("mulberry32 es determinista", () => {
    const a = mulberry32(42);
    const b = mulberry32(42);
    expect([a(), a(), a()]).toEqual([b(), b(), b()]);
  });

  it("percentil con interpolación lineal como numpy", () => {
    expect(percentile([1, 2, 3, 4], 50)).toBe(2.5);
    expect(percentile([10], 90)).toBe(10);
    expect(Number.isNaN(percentile([], 50))).toBe(true);
  });

  it("bootstrap con retornos constantes da trayectorias exactas", () => {
    const r = bootstrap([0.01, 0.01, 0.01], { nSimulations: 5, seed: 1, blockSize: 2, horizon: 10, initialCapital: 100, fanIndices: [0, 5, 10] });
    expect(r.finalReturns[0]).toBeCloseTo(1.01 ** 10 - 1);
    expect(r.maxDrawdowns[0]).toBe(0);
    expect(r.fan[0]![0]).toBe(100);
    expect(r.fan[2]![0]).toBeCloseTo(100 * 1.01 ** 10);
    expect(r.peakGrowth[0]).toBeCloseTo(1.01 ** 10);
    expect(() => bootstrap([], { nSimulations: 1, seed: 1, blockSize: 1 })).toThrow();
  });

  it("histograma incluye el máximo en el último intervalo", () => {
    const bins = histogram([0, 0.5, 1], 2, 0, 1);
    expect(bins.map((b) => b.count)).toEqual([1, 2]);
    expect(histogram([0, 0], 2, 0, 0)[0]!.count).toBe(2);
    expect(stdSample([1])).toBe(0);
  });
});

describe("riesgo y exposición", () => {
  it("drawdown por nivel", () => {
    expect(levelDrawdown(1)).toBe(0.1);
    expect(() => levelDrawdown(9)).toThrow();
  });

  it("exposición con y sin curva", () => {
    expect(exposureForLevel(5, 0.3)).toBe(1);
    expect(exposureForLevel(3, null)).toBe(0);
    expect(exposureForLevel(3, 0.4)).toBeCloseTo(0.5 / 1.3);
    const k = exposureForLevel(3, 0.4383, TREND_SENSITIVITY);
    expect(k).toBeGreaterThan(0.4);
    expect(k).toBeLessThan(0.5);
    expect(exposureForLevel(3, 0.4, [[0.5, 0.2]])).toBeCloseTo(0.5 / 1.3);
    expect(exposureForLevel(3, 0.4, [[1, 0.4], [0.5, 0.5]])).toBeCloseTo(0.5 / 1.3);
    expect(sensitivityFor("trend", null)).toBe(TREND_SENSITIVITY);
    expect(sensitivityFor("x", [[1, 0.2]])).toEqual([[1, 0.2]]);
    expect(sensitivityFor("x", null)).toBeNull();
  });
});

describe("allocator", () => {
  const base = { horizon: null, max_drawdown_p95: null };

  it("inversa de la volatilidad y la mitad para probation", () => {
    const a = allocate(1000, "medium", [
      { ...base, name: "a", state: "active", volatility: 0.2, oos_sharpe: 1 },
      { ...base, name: "b", state: "active", volatility: 0.4, oos_sharpe: 1 },
    ]);
    expect(a.weights.a).toBeCloseTo(2 / 3);
    const p = allocate(1000, "medium", [
      { ...base, name: "a", state: "active", volatility: 0.2, oos_sharpe: 1 },
      { ...base, name: "b", state: "probation", volatility: 0.2, oos_sharpe: 1 },
    ]);
    expect(p.weights.b).toBeCloseTo(0.25);
    expect(p.weights.a).toBeCloseTo(0.75);
  });

  it("excluye por estado, volatilidad, Sharpe, horizonte y nocional mínimo", () => {
    const a = allocate(15, "short", [
      { ...base, name: "s", state: "shadow", volatility: 0.2, oos_sharpe: 1 },
      { ...base, name: "v", state: "active", volatility: 0, oos_sharpe: 1 },
      { ...base, name: "sh", state: "active", volatility: 0.2, oos_sharpe: -1 },
      { name: "h1", state: "active", volatility: 0.2, oos_sharpe: 1, horizon: "long", max_drawdown_p95: null },
      { name: "h2", state: "active", volatility: 0.2, oos_sharpe: 1, horizon: "short", max_drawdown_p95: null },
    ]);
    expect(Object.keys(a.excluded).sort()).toEqual(["h1", "s", "sh", "v"]);
    expect(a.excluded.h1).toContain("nocional mínimo");
    expect(a.weights.h2).toBe(1);
  });
});

describe("lab-core", () => {
  const report = {
    config: { strategy: "x", symbols: ["BTCUSDT"], seed: 1 },
    aggregate: { sharpe: 1 },
    approval: { approved: true, reasons: [], checks: [{ ok: true }], exposure_by_risk_level: { "1": 0.2, bad: "x" }, criteria: { max_drawdown_p95: 0.4 } },
    montecarlo: { n_simulations: 200, block_size: 5, horizon_days: 50, max_drawdown: { p95: 0.1 } },
    equity_daily: {
      strategy: series(60).map(([d], i) => [d, 100 + i]),
      buy_and_hold: [["2024-01-01", 1]],
    },
    windows: [{ label: "w1", oos: { total_return: 0.1 } }],
  };

  it("resumen y detalle", () => {
    const s = summary("x", report);
    expect(s).toMatchObject({ strategy: "x", approved: true, max_drawdown_p95: 0.1 });
    const d = detail("x", report);
    expect(d.exposure_by_risk_level).toEqual({ "1": 0.2 });
    expect(d.equity[0]!.buy_and_hold).toBe(1);
    expect(d.equity[1]!.buy_and_hold).toBeNull();
    expect(d.windows[0]).toMatchObject({ label: "w1", oos_return: 0.1 });
    expect(d.drawdown_histogram?.limit).toBe(0.4);
    expect(summary("y", {}).strategy).toBe("y");
  });

  it("sin datos suficientes no hay histograma", () => {
    expect(drawdownHistogram({ equity_daily: { strategy: [["2024-01-01", 1]] } })).toBeNull();
    expect(returnsFromEquity([["2024-01-02", 2], ["2024-01-01", 1], ["2024-01-03", 0]])).toEqual([["2024-01-02", 1]]);
  });
});

describe("proyección", () => {
  const strategy = (name: string, n: number, report: Record<string, unknown> | null = null): StrategySeries => ({
    name,
    state: "active",
    lab_report: report ?? { aggregate: { volatility: 0.3, sharpe: 1 }, montecarlo: { max_drawdown: { p95: 0.3 } } },
    returns: series(n),
    source: "lab_report",
    report_source: "db",
  });
  const input = {
    startEquity: 1000,
    targetEquity: 1100,
    riskLevel: 3,
    horizon: "medium" as const,
    today: "2026-01-01",
    deadline: "2026-07-01",
  };

  it("es determinista y entrega el abanico", () => {
    const a = projectGoal({ ...input, strategies: [strategy("a", 200), strategy("b", 150), strategy("c", 10)] });
    const b = projectGoal({ ...input, strategies: [strategy("a", 200), strategy("b", 150), strategy("c", 10)] });
    expect(a.probability).toBe(b.probability);
    expect(a.probability).toBeGreaterThan(0);
    expect(a.probability).toBeGreaterThanOrEqual(a.probability_at_deadline);
    expect(a.fan[0]).toMatchObject({ day: "2026-01-01", p50: 1000 });
    expect(a.fan.at(-1)!.day).toBe("2026-07-01");
    expect(a.strategies.find((s) => s.name === "c")!.excluded).toContain("sin historial");
    expect(a.notes.length).toBe(1);
  });

  it("usa Sharpe plano o de la serie si el reporte está resumido", () => {
    const flat = projectGoal({ ...input, strategies: [strategy("a", 100, { sharpe_oos: 0.8, max_drawdown_p95: 0.2 })] });
    expect(flat.invested_fraction).toBeGreaterThan(0);
    const none = projectGoal({ ...input, strategies: [strategy("a", 100, {})] });
    expect(none.invested_fraction).toBe(0);
    expect(none.strategies[0]!.excluded).toBeTruthy();
  });

  it("sin estrategias el equity no cambia", () => {
    const r = projectGoal({ ...input, strategies: [] });
    expect(r).toMatchObject({ probability: 0, n_simulations: 0, final_equity_p50: 1000 });
    expect(projectGoal({ ...input, targetEquity: 900, strategies: [] }).probability).toBe(1);
  });

  it("errores de proyección", () => {
    expect(() => projectGoal({ ...input, deadline: "2025-01-01", strategies: [] })).toThrow(ProjectionError);
    expect(() => projectGoal({ ...input, deadline: "2040-01-01", strategies: [] })).toThrow(/10 años/);
    expect(() => projectGoal({ ...input, startEquity: 0, strategies: [] })).toThrow(/mayor que 0/);
  });

  it("simulaciones y abanico", () => {
    expect(simulationsFor(365)).toBe(2000);
    expect(simulationsFor(3653)).toBe(500);
    expect(fanIndices(5)).toEqual([0, 1, 2, 3, 4, 5]);
    expect(fanIndices(365)).toHaveLength(60);
  });

  it("effectiveReport cae al archivo del laboratorio", () => {
    expect(effectiveReport({ name: "trend", lab_report: { sharpe_oos: 1 } })[1]).toBe("file");
    expect(effectiveReport({ name: "nada", lab_report: null })[1]).toBe("none");
    expect(effectiveReport({ name: "nada", lab_report: { x: 1 } })[1]).toBe("db");
  });
});

describe("ciclo de vida", () => {
  it("transiciones permitidas y días en shadow", () => {
    expect(isAllowedTransition("shadow", "active")).toBe(true);
    expect(isAllowedTransition("lab", "active")).toBe(false);
    expect(shadowDaysClean({ state: "active" })).toBeNull();
    expect(shadowDaysClean({ state: "shadow", state_changed_at: "x" })).toBe(0);
  });
});
