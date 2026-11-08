import { describe, expect, it } from "vitest";
import {
  botStatusLabel,
  formatDateTime,
  formatDay,
  formatMoney,
  formatPct,
  formatQty,
  formatSignedMoney,
  formatSymbol,
  stateLabel,
  strategyLabel,
  toNumber,
} from "./format";

describe("format", () => {
  it("toNumber acepta numeric de PostgREST como texto", () => {
    expect(toNumber("12.5")).toBe(12.5);
    expect(toNumber(3)).toBe(3);
    expect(toNumber(null)).toBeNull();
    expect(toNumber("")).toBeNull();
    expect(toNumber("abc")).toBeNull();
  });

  it("formatea dinero, porcentajes y cantidades", () => {
    expect(formatMoney(1234.5)).toBe("1,234.50");
    expect(formatMoney(1234.5, 0)).toBe("1,235");
    expect(formatMoney(null)).toBe("—");
    expect(formatSignedMoney(12.3)).toBe("+12.30");
    expect(formatSignedMoney(-4)).toBe("-4.00");
    expect(formatSignedMoney(0)).toBe("0.00");
    expect(formatSignedMoney(undefined)).toBe("—");
    expect(formatPct(0.1234)).toBe("12.3 %");
    expect(formatPct(0.05, 0, true)).toBe("+5 %");
    expect(formatPct(Number.NaN)).toBe("—");
    expect(formatQty(0.123456789)).toBe("0.123457");
    expect(formatQty(null)).toBe("—");
  });

  it("formatea fechas y días", () => {
    expect(formatDateTime(null)).toBe("—");
    expect(formatDateTime("no")).toBe("—");
    expect(formatDateTime("2026-11-08T10:00:00Z")).toMatch(/2026/);
    expect(formatDay("2026-11-08")).toMatch(/8/);
    expect(formatDay("2026-11-08T10:00:00Z")).toMatch(/8/);
    expect(formatDay(undefined)).toBe("—");
    expect(formatDay("x")).toBe("—");
  });

  it("nombra símbolos, estrategias y estados", () => {
    expect(formatSymbol("BTCUSDT")).toBe("BTC/USDT");
    expect(formatSymbol("XYZ")).toBe("XYZ");
    expect(strategyLabel("trend")).toBe("Tendencia");
    expect(strategyLabel("nueva")).toBe("nueva");
    expect(stateLabel("probation")).toBe("En observación");
    expect(stateLabel("otro")).toBe("otro");
    expect(botStatusLabel("killed")).toBe("Paro de emergencia");
    expect(botStatusLabel("otro")).toBe("otro");
  });
});
