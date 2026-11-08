import { describe, expect, it } from "vitest";
import { fakeFetcher } from "@/test/fakeApi";
import { EMPTY_BACKTEST_FORM, createBacktest, getLabReport, validateBacktest } from "./api";

describe("validateBacktest", () => {
  it("arma el config con capital opcional", () => {
    const r = validateBacktest({ ...EMPTY_BACKTEST_FORM, initialCapital: "500" });
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.body.config).toMatchObject({ symbols: ["BTCUSDT", "ETHUSDT"], initial_capital: "500" });
  });

  it("omite el capital vacío", () => {
    const r = validateBacktest(EMPTY_BACKTEST_FORM);
    expect(r.ok && "initial_capital" in r.body.config).toBe(false);
  });

  it("rechaza sin instrumentos, fechas invertidas y capital negativo", () => {
    const r = validateBacktest({
      ...EMPTY_BACKTEST_FORM,
      symbols: [],
      start: "2025-01-01",
      end: "2024-01-01",
      initialCapital: "-3",
    });
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.errors.symbols).toBe("Elige al menos un instrumento");
      expect(r.errors.end).toBe("El fin debe ser posterior al inicio");
      expect(r.errors.initialCapital).toBe("Debe ser mayor que 0");
    }
  });
});

describe("cliente de la API", () => {
  it("codifica el nombre del reporte y manda el token", async () => {
    const fetcher = fakeFetcher({ "GET /lab/reports/a%20b": { name: "a b" } });
    await getLabReport("tok", "a b", fetcher);
    const [, init] = fetcher.mock.calls[0];
    expect((init as RequestInit).headers).toMatchObject({ Authorization: "Bearer tok" });
  });

  it("propaga el detail de FastAPI como error", async () => {
    const fetcher = fakeFetcher({ "POST /backtests": { status: 422, body: { detail: "malo" } } });
    await expect(createBacktest("tok", { config: {} }, fetcher)).rejects.toThrow("malo");
  });
});
