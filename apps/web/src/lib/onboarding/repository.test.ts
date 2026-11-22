import { describe, expect, it } from "vitest";
import { mockApi } from "@/test/api";
import { fetchProfile, OnboardingSaveError, profileToForm, saveOnboarding, toOnboardingBody } from "./repository";

const values = { capital: 1500, riskLevel: 2 as const, goals: [{ targetReturnPct: 12.5, deadline: "2099-01-01" }] };

describe("repositorio del onboarding", () => {
  it("arma el cuerpo de PUT /api/onboarding con el retorno como fracción", () => {
    expect(toOnboardingBody(values)).toEqual({
      risk_level: 2,
      capital: 1500,
      goals: [{ target_return: 0.125, deadline: "2099-01-01" }],
    });
  });

  it("convierte un perfil guardado en el estado del formulario", () => {
    expect(
      profileToForm({ id: "p1", risk_level: 4, capital: "2000", goals: [{ target_return: "0.1", deadline: "2099-01-01" }] }),
    ).toEqual({ capital: "2000", riskLevel: "4", goals: [{ targetReturnPct: "10", deadline: "2099-01-01" }] });
    expect(profileToForm({ id: "p1", risk_level: 9, capital: 600, goals: null })).toEqual({
      capital: "600",
      riskLevel: "3",
      goals: [{ targetReturnPct: "", deadline: "" }],
    });
  });

  it("lee el perfil (o null) de GET /api/onboarding", async () => {
    mockApi({ "GET /onboarding": { body: null } });
    await expect(fetchProfile()).resolves.toBeNull();
    mockApi({ "GET /onboarding": { id: "p1", risk_level: 3, capital: 1000, goals: [] } });
    await expect(fetchProfile()).resolves.toMatchObject({ id: "p1" });
  });

  it("guarda con PUT y devuelve el id del perfil", async () => {
    const { calls } = mockApi({ "PUT /onboarding": { profileId: "p9", profile_id: "p9" } });
    await expect(saveOnboarding(values)).resolves.toEqual({ profileId: "p9" });
    expect(calls.at(-1)).toMatchObject({ method: "PUT", path: "/onboarding" });
  });

  it("envuelve los errores con un mensaje para el usuario", async () => {
    mockApi({ "PUT /onboarding": { status: 422, body: { detail: "capital debe ser >= 500" } } });
    const err = await saveOnboarding(values).catch((e) => e);
    expect(err).toBeInstanceOf(OnboardingSaveError);
    expect(err.message).toBe("No se pudo guardar tu perfil. capital debe ser >= 500");
  });
});
