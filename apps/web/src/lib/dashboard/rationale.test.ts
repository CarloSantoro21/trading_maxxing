import { describe, expect, it } from "vitest";
import { asRationale, describeRationale } from "./rationale";

describe("describeRationale", () => {
  it("explica señales, pesos, simulación y riesgo en lenguaje claro", () => {
    const items = describeRationale({
      sleeve: "trend",
      signals: [
        { symbol: "BTCUSDT", direction: "long", confidence: 0.72, timeframe: "1d", source: "trend" },
        { symbol: "ETHUSDT", direction: "flat", source: "kronos" },
        { direction: "rara" },
      ],
      weights: { trend: 0.6, kronos: 0.4, malo: "x" },
      news_veto: false,
      sim: { p_take_profit: 0.58, p_stop_loss: 0.3, p_open: 0.12, expected_value: 0.25, n_paths: 1000, source: "kronos" },
      risk: { approved: true, reasons: [] },
      text: "Tendencia alcista clara.",
    });
    expect(items.map((i) => i.label)).toEqual([
      "Señal",
      "Señal",
      "Señal",
      "Peso",
      "Simulación",
      "Riesgo",
      "Explicación",
    ]);
    expect(items[0].text).toBe("Tendencia recomienda comprar BTC/USDT con 72 % de confianza (velas de 1d).");
    expect(items[1].text).toBe("Kronos (pronóstico) recomienda quedarse fuera ETH/USDT.");
    expect(items[2].text).toBe("estrategia da la señal rara.");
    expect(items[3].text).toBe("Reparto del capital entre estrategias: Tendencia 60 %, Kronos (pronóstico) 40 %.");
    expect(items[4]).toMatchObject({ tone: "good" });
    expect(items[4].text).toContain("58 % de probabilidad de llegar al objetivo y 30 % de tocar el stop");
    expect(items[4].text).toContain("+0.25 veces lo arriesgado");
    expect(items[4].text).toContain("1,000 escenarios, pronóstico de Kronos");
    expect(items[5]).toEqual({ label: "Riesgo", text: "El control de riesgo la aprobó (dentro de tus límites).", tone: "good" });
  });

  it("marca el veto de noticias, una simulación negativa y el rechazo de riesgo", () => {
    const items = describeRationale({
      news_veto: true,
      news_summary: "hackeo en un exchange",
      sim: { expected_value: -0.1 },
      risk: { approved: false, reasons: ["exposición máxima"] },
    });
    expect(items).toEqual([
      { label: "Noticias", text: "Las noticias bloquearon nuevas compras: hackeo en un exchange", tone: "bad" },
      { label: "Simulación", text: "Antes de operar se simuló el precio: ganancia esperada de -0.10 veces lo arriesgado.", tone: "bad" },
      { label: "Riesgo", text: "El control de riesgo la rechazó: exposición máxima", tone: "bad" },
    ]);
  });

  it("maneja rationale vacío, incompleto o con otro tipo", () => {
    expect(describeRationale(null)).toEqual([]);
    expect(describeRationale([1, 2])).toEqual([]);
    expect(asRationale("x")).toEqual({});
    const items = describeRationale({
      news_veto: true,
      news_summary: null,
      sim: { source: "otra" },
      risk: { approved: true, reasons: ["ok"] },
    });
    expect(items[0].text).toBe("Las noticias bloquearon nuevas compras.");
    expect(items[1]).toEqual({ label: "Simulación", text: "Antes de operar se simuló el precio: sin datos (otra).", tone: "neutral" });
    expect(items[2].text).toBe("El control de riesgo la aprobó: ok");
    expect(describeRationale({ news_summary: "tranquilo", risk: { approved: false } })).toEqual([
      { label: "Noticias", text: "Sin veto. tranquilo", tone: "neutral" },
      { label: "Riesgo", text: "El control de riesgo la rechazó.", tone: "bad" },
    ]);
  });
});
