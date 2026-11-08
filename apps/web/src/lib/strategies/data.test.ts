import { describe, expect, it } from "vitest";
import { createFakeSupabase } from "@/test/fakeSupabase";
import {
  actorLabel,
  availableActions,
  bandPosition,
  drawdownBand,
  fetchStrategies,
  labSummary,
  latestRun,
  mcBand,
  referenceSeries,
  StrategiesLoadError,
  type ReferenceEquityRow,
} from "./data";

const report = {
  montecarlo: {
    horizon_days: 365,
    final_return: { p5: -0.1, p50: 0.1, p95: 0.3 },
    max_drawdown: { p5: 0.05, p50: 0.1, p95: 0.2, p99: 0.25 },
  },
  aggregate: { sharpe: 1.2, cagr: 0.15, max_drawdown: 0.18, trades: 42, oos_years: 3, start: "2022-01-01", end: "2025-12-31" },
  approval: { approved: true, checks: [{ name: "sharpe", passed: true, reason: "Sharpe ≥ 1" }, null, { passed: false }] },
};

describe("fetchStrategies", () => {
  it("normaliza las filas y detecta al admin", async () => {
    const fake = createFakeSupabase((q) => {
      if (q.table === "strategies")
        return { data: [{ id: "s1", name: "trend", state: "shadow", params: null, lab_report: null, live_metrics: null, state_changed_at: "x", shadow_days_clean: "7.5" }] };
      if (q.table === "app_roles") return { data: { role: "admin" } };
      return { data: null };
    });
    const data = await fetchStrategies(fake.client, "u1");
    expect(data.isAdmin).toBe(true);
    expect(data.strategies[0]).toMatchObject({ params: {}, live_metrics: {}, shadow_days_clean: 7.5 });
    expect(data.transitions).toEqual([]);
  });

  it("si no puede leer el rol sigue en modo lectura", async () => {
    const fake = createFakeSupabase((q) => (q.table === "app_roles" ? { error: { message: "rls" } } : { data: [] }));
    await expect(fetchStrategies(fake.client, "u1")).resolves.toMatchObject({ isAdmin: false });
  });

  it("falla si no puede leer las estrategias", async () => {
    const fake = createFakeSupabase((q) => (q.table === "strategies" ? { error: { message: "x" } } : { data: [] }));
    const err = await fetchStrategies(fake.client, "u1").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(StrategiesLoadError);
    expect((err as Error).message).toBe("No se pudo leer las estrategias.");
  });
});

describe("availableActions", () => {
  it("promueve desde shadow solo con 7 días limpios", () => {
    expect(availableActions({ state: "shadow", shadow_days_clean: 7 })).toEqual({ canPromote: true, promoteHint: null, canRetire: true });
    const early = availableActions({ state: "shadow", shadow_days_clean: 2.25 });
    expect(early.canPromote).toBe(false);
    expect(early.promoteHint).toBe("Lleva 2.3 de 7 días en shadow sin fallas.");
    expect(availableActions({ state: "shadow", shadow_days_clean: null }).promoteHint).toContain("0.0");
  });

  it("probation se promueve; active solo se retira; retired no ofrece nada", () => {
    expect(availableActions({ state: "probation", shadow_days_clean: null }).canPromote).toBe(true);
    expect(availableActions({ state: "active", shadow_days_clean: null })).toEqual({ canPromote: false, promoteHint: null, canRetire: true });
    expect(availableActions({ state: "retired", shadow_days_clean: null }).canRetire).toBe(false);
  });
});

describe("bandas del Monte Carlo", () => {
  it("mcBand lee el retorno final y rechaza reportes incompletos", () => {
    expect(mcBand(report)).toEqual({ horizonDays: 365, p5: -0.1, p50: 0.1, p95: 0.3 });
    expect(mcBand({ montecarlo: { horizon_days: 365, final_return: { p5: 0, p95: 0.2 } } })?.p50).toBe(0.1);
    expect(mcBand(null)).toBeNull();
    expect(mcBand({ montecarlo: { horizon_days: 0, final_return: { p5: 0, p95: 1 } } })).toBeNull();
  });

  it("drawdownBand acepta la distribución o los campos planos", () => {
    expect(drawdownBand(report)).toEqual({ p5: 0.05, p50: 0.1, p95: 0.2, p99: 0.25 });
    expect(drawdownBand({ max_drawdown_p95: 0.3, max_drawdown_p99: 0.4 })).toEqual({ p5: null, p50: null, p95: 0.3, p99: 0.4 });
    expect(drawdownBand({})).toBeNull();
    expect(drawdownBand(null)).toBeNull();
  });

  const rows: ReferenceEquityRow[] = [
    { strategy_id: "s1", started_at: "2026-01-01T00:00:00Z", day: "2026-01-01", equity: 1, drawdown: 0 },
    { strategy_id: "s1", started_at: "2026-10-01T00:00:00Z", day: "2027-10-01", equity: "1500", drawdown: "0" },
    { strategy_id: "s1", started_at: "2026-10-01T00:00:00Z", day: "2026-10-01", equity: "1000", drawdown: "0" },
    { strategy_id: "s2", started_at: "2026-10-01T00:00:00Z", day: "2026-10-01", equity: 5, drawdown: 0 },
  ];

  it("latestRun toma la corrida más reciente, ordenada por día", () => {
    expect(latestRun(rows, "s1").map((r) => r.day)).toEqual(["2026-10-01", "2027-10-01"]);
    expect(latestRun(rows, "nada")).toEqual([]);
  });

  it("referenceSeries interpola la banda y bandPosition ubica el último punto", () => {
    const points = referenceSeries(latestRun(rows, "s1"), mcBand(report));
    expect(points[0].band).toEqual([1000, 1000]);
    expect(points[1].band?.[0]).toBeCloseTo(900);
    expect(points[1].band?.[1]).toBeCloseTo(1300);
    expect(bandPosition(points)).toBe("above");
    const inside = referenceSeries([{ ...rows[2] }, { ...rows[1], equity: 1100 }], mcBand(report));
    expect(bandPosition(inside)).toBe("inside");
    const below = referenceSeries([{ ...rows[2] }, { ...rows[1], equity: 800 }], mcBand(report));
    expect(bandPosition(below)).toBe("below");
    const noBand = referenceSeries(latestRun(rows, "s1"), null);
    expect(noBand[0].band).toBeUndefined();
    expect(bandPosition(noBand)).toBeNull();
    expect(referenceSeries([], null)).toEqual([]);
  });
});

describe("labSummary y actorLabel", () => {
  it("resume el reporte del laboratorio", () => {
    expect(labSummary(report)).toEqual({
      approved: true,
      sharpe: 1.2,
      cagr: 0.15,
      maxDrawdown: 0.18,
      trades: 42,
      oosYears: 3,
      start: "2022-01-01",
      end: "2025-12-31",
      checks: [
        { name: "sharpe", passed: true, reason: "Sharpe ≥ 1" },
        { name: "", passed: false, reason: "" },
      ],
    });
    expect(labSummary({ sharpe_oos: 0.9 })).toMatchObject({ approved: null, sharpe: 0.9, checks: [], start: null });
    expect(labSummary(null)).toBeNull();
  });

  it("nombra al actor de una transición", () => {
    expect(actorLabel("system", "u1")).toBe("Sistema (automático)");
    expect(actorLabel("u1", "u1")).toBe("Tú");
    expect(actorLabel("12345678-aaaa", "u1")).toBe("Admin 12345678");
  });
});
