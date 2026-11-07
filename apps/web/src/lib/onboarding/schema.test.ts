import { describe, expect, it } from "vitest";
import {
  EMPTY_FORM,
  errorsForStep,
  fractionToPct,
  pctToFraction,
  todayIso,
  validateOnboarding,
  type OnboardingFormState,
} from "./schema";

const valid: OnboardingFormState = {
  capital: "10000",
  horizon: "medium",
  riskLevel: "3",
  maxDrawdownPct: "20",
  instruments: ["BTCUSDT", "ETHUSDT"],
  goals: [{ targetReturnPct: "15", deadline: "2099-12-31" }],
};

function errorsOf(form: OnboardingFormState) {
  const result = validateOnboarding(form);
  if (result.success) throw new Error("se esperaba un error de validación");
  return result.errors;
}

describe("validateOnboarding", () => {
  it("acepta un formulario válido y convierte los números", () => {
    const result = validateOnboarding(valid);
    expect(result.success).toBe(true);
    if (!result.success) return;
    expect(result.data).toEqual({
      capital: 10000,
      horizon: "medium",
      riskLevel: 3,
      maxDrawdownPct: 20,
      instruments: ["BTCUSDT", "ETHUSDT"],
      goals: [{ targetReturnPct: 15, deadline: "2099-12-31" }],
    });
  });

  it("acepta coma decimal", () => {
    const result = validateOnboarding({ ...valid, capital: "1500,5" });
    expect(result.success && result.data.capital).toBe(1500.5);
  });

  it.each([
    ["", "Escribe el capital inicial."],
    ["abc", "Escribe el capital inicial."],
    ["0", "El capital debe ser mayor que 0."],
    ["-5", "El capital debe ser mayor que 0."],
  ])("rechaza capital %j (check capital > 0)", (capital, message) => {
    expect(errorsOf({ ...valid, capital }).capital).toBe(message);
  });

  it("exige horizonte short, medium o long", () => {
    expect(errorsOf({ ...valid, horizon: "" }).horizon).toBe("Elige un horizonte de inversión.");
  });

  it.each(["0", "6", "2.5"])("rechaza nivel de riesgo %s (check 1..5 entero)", (riskLevel) => {
    expect(errorsOf({ ...valid, riskLevel }).riskLevel).toMatch(/entre 1 y 5/);
  });

  it.each(["0", "-1", "100.1"])("rechaza drawdown %s %% (check 0 < x <= 1)", (maxDrawdownPct) => {
    expect(errorsOf({ ...valid, maxDrawdownPct }).maxDrawdownPct).toMatch(/drawdown máximo/);
  });

  it("acepta drawdown de 100 %", () => {
    expect(validateOnboarding({ ...valid, maxDrawdownPct: "100" }).success).toBe(true);
  });

  it("exige al menos un instrumento soportado y sin repetir", () => {
    expect(errorsOf({ ...valid, instruments: [] }).instruments).toBe("Elige al menos un instrumento.");
    expect(errorsOf({ ...valid, instruments: ["BTCUSDT", "BTCUSDT"] }).instruments).toBe(
      "Hay instrumentos repetidos.",
    );
    const unsupported = { ...valid, instruments: ["SOLUSDT"] } as unknown as OnboardingFormState;
    expect(errorsOf(unsupported)["instruments.0"]).toBe("Instrumento no soportado.");
  });

  it("valida cada meta: retorno > 0 y fecha futura", () => {
    const errors = errorsOf({
      ...valid,
      goals: [
        { targetReturnPct: "0", deadline: "2000-01-01" },
        { targetReturnPct: "", deadline: "" },
      ],
    });
    expect(errors["goals.0.targetReturnPct"]).toMatch(/mayor que 0/);
    expect(errors["goals.0.deadline"]).toBe("La fecha límite debe ser posterior a hoy.");
    expect(errors["goals.1.targetReturnPct"]).toBe("Escribe el retorno objetivo.");
    expect(errors["goals.1.deadline"]).toBe("Elige una fecha límite.");
  });

  it("rechaza fechas con formato correcto pero inexistentes", () => {
    const errors = errorsOf({ ...valid, goals: [{ targetReturnPct: "5", deadline: "2099-13-45" }] });
    expect(errors["goals.0.deadline"]).toBe("La fecha no es válida.");
  });

  it("exige entre 1 y 5 metas", () => {
    expect(errorsOf({ ...valid, goals: [] }).goals).toBe("Agrega al menos una meta.");
    const six = Array.from({ length: 6 }, () => valid.goals[0]);
    expect(errorsOf({ ...valid, goals: six }).goals).toMatch(/hasta 5 metas/);
  });

  it("el formulario vacío falla en capital, horizonte y metas", () => {
    const errors = errorsOf(EMPTY_FORM);
    expect(Object.keys(errors)).toEqual(
      expect.arrayContaining(["capital", "horizon", "goals.0.targetReturnPct", "goals.0.deadline"]),
    );
    expect(errors.riskLevel).toBeUndefined();
    expect(errors.maxDrawdownPct).toBeUndefined();
  });
});

describe("errorsForStep", () => {
  it("filtra los errores por los campos del paso", () => {
    const errors = { capital: "a", riskLevel: "b", "goals.0.deadline": "c", instruments: "d" };
    expect(errorsForStep(errors, 0)).toEqual({ capital: "a" });
    expect(errorsForStep(errors, 1)).toEqual({ riskLevel: "b" });
    expect(errorsForStep(errors, 2)).toEqual({ instruments: "d" });
    expect(errorsForStep(errors, 3)).toEqual({ "goals.0.deadline": "c" });
  });
});

describe("conversiones", () => {
  it("convierte porcentaje a fracción y de regreso sin ruido de punto flotante", () => {
    expect(pctToFraction(15)).toBe(0.15);
    expect(pctToFraction(0.1 + 0.2)).toBe(0.003);
    expect(fractionToPct(0.15)).toBe(15);
    expect(fractionToPct(0.07)).toBe(7);
  });

  it("todayIso da la fecha local en YYYY-MM-DD", () => {
    expect(todayIso(new Date(2026, 0, 5))).toBe("2026-01-05");
  });
});
