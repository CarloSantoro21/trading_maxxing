import { describe, expect, it } from "vitest";
import { downsample, histogramBars, normalizeEquity, num, pct, usd } from "./format";

describe("format", () => {
  it("formatea porcentajes, números y USDT con guion si falta el valor", () => {
    expect(pct(0.1234)).toBe("12.3 %");
    expect(pct(null)).toBe("—");
    expect(num(Number.NaN)).toBe("—");
    expect(num(1.5)).toBe("1.50");
    expect(usd(undefined)).toBe("—");
    expect(usd(1200)).toContain("USDT");
  });

  it("submuestrea conservando extremos", () => {
    const rows = Array.from({ length: 1000 }, (_, i) => i);
    const out = downsample(rows, 10);
    expect(out).toHaveLength(10);
    expect(out[0]).toBe(0);
    expect(out[9]).toBe(999);
    expect(downsample([1, 2], 10)).toEqual([1, 2]);
  });

  it("normaliza la equity a crecimiento de 1", () => {
    const out = normalizeEquity([
      { day: "a", strategy: 100, buy_and_hold: null },
      { day: "b", strategy: 200, buy_and_hold: 50 },
      { day: "c", strategy: 150, buy_and_hold: 100 },
    ]);
    expect(out.map((r) => r.strategy)).toEqual([1, 2, 1.5]);
    expect(out.map((r) => r.buy_and_hold)).toEqual([null, 1, 2]);
  });

  it("marca la cola del histograma", () => {
    const bars = histogramBars(
      [
        { start: 0, end: 0.1, count: 1 },
        { start: 0.3, end: 0.4, count: 2 },
      ],
      0.25,
    );
    expect(bars.map((b) => b.tail)).toEqual([false, true]);
    expect(bars[0].mid).toBeCloseTo(5);
  });
});
