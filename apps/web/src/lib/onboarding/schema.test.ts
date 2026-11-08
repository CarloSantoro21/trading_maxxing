import { describe, expect, it } from "vitest";
import {
  EMPTY_FORM,
  errorsForStep,
  formatUsd,
  fractionToPct,
  isRiskLevel,
  maxLossUsd,
  MIN_CAPITAL,
  parseCapital,
  pctToFraction,
  RISK_LEVELS,
  RISK_TABLE,
  STEPS,
  todayIso,
  validateOnboarding,
  type OnboardingFormState,
} from "./schema";

const valid: OnboardingFormState = {
  capital: "10000",
  riskLevel: "3",
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
      riskLevel: 3,
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
    ["0", "El capital mínimo es de 500 USDT."],
    ["-5", "El capital mínimo es de 500 USDT."],
    ["499.99", "El capital mínimo es de 500 USDT."],
  ])("rechaza capital %j (check capital >= 500)", (capital, message) => {
    expect(errorsOf({ ...valid, capital }).capital).toBe(message);
  });

  it("acepta exactamente el capital mínimo", () => {
    expect(validateOnboarding({ ...valid, capital: String(MIN_CAPITAL) }).success).toBe(true);
  });

  it.each(["0", "6", "2.5", ""])("rechaza nivel de riesgo %j (check 1..5 entero)", (riskLevel) => {
    expect(errorsOf({ ...valid, riskLevel }).riskLevel).toMatch(/nivel de riesgo/);
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

  it("el formulario vacío falla en capital y metas, no en el riesgo (3 por defecto)", () => {
    const errors = errorsOf(EMPTY_FORM);
    expect(Object.keys(errors)).toEqual(
      expect.arrayContaining(["capital", "goals.0.targetReturnPct", "goals.0.deadline"]),
    );
    expect(errors.riskLevel).toBeUndefined();
  });

  it("ya no captura horizonte, drawdown ni instrumentos", () => {
    expect(Object.keys(EMPTY_FORM).sort()).toEqual(["capital", "goals", "riskLevel"]);
  });
});

describe("pasos", () => {
  it("son tres: capital, nivel de riesgo y metas", () => {
    expect(STEPS.map((s) => s.id)).toEqual(["capital", "risk", "goals"]);
  });

  it("errorsForStep filtra los errores por los campos del paso", () => {
    const errors = { capital: "a", riskLevel: "b", "goals.0.deadline": "c" };
    expect(errorsForStep(errors, 0)).toEqual({ capital: "a" });
    expect(errorsForStep(errors, 1)).toEqual({ riskLevel: "b" });
    expect(errorsForStep(errors, 2)).toEqual({ "goals.0.deadline": "c" });
  });
});

describe("niveles de riesgo", () => {
  it("coinciden con la tabla de la propuesta (sección 3.4)", () => {
    expect(RISK_LEVELS.map((l) => RISK_TABLE[l].maxDrawdownPct)).toEqual([10, 15, 20, 30, 40]);
    expect(RISK_LEVELS.map((l) => RISK_TABLE[l].riskPerTradePct)).toEqual([0.25, 0.5, 1, 1.5, 2]);
    expect(RISK_LEVELS.map((l) => RISK_TABLE[l].maxExposurePct)).toEqual([20, 35, 50, 70, 90]);
    for (const l of RISK_LEVELS) expect(RISK_TABLE[l].summary.length).toBeGreaterThan(10);
  });

  it("isRiskLevel solo acepta 1..5", () => {
    expect(RISK_LEVELS.every(isRiskLevel)).toBe(true);
    expect([0, 6, 2.5, Number.NaN].some(isRiskLevel)).toBe(false);
  });

  it("calcula la caída máxima en dólares para el capital", () => {
    expect(maxLossUsd(500, 1)).toBe(50);
    expect(maxLossUsd(10000, 3)).toBe(2000);
    expect(maxLossUsd(1234.56, 5)).toBe(493.82);
  });

  it("formatea montos con separador de miles", () => {
    expect(formatUsd(10000)).toBe("10,000");
    expect(formatUsd(1234.567)).toBe("1,234.57");
  });

  it("parseCapital da null si el texto no es un monto positivo", () => {
    expect(parseCapital("1500,5")).toBe(1500.5);
    expect(parseCapital("")).toBeNull();
    expect(parseCapital("abc")).toBeNull();
    expect(parseCapital("-3")).toBeNull();
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
