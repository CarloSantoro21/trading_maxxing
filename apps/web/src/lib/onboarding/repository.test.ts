import { describe, expect, it } from "vitest";
import { createFakeSupabase } from "@/test/fakeSupabase";
import {
  fetchProfile,
  OnboardingSaveError,
  profileToForm,
  saveOnboarding,
  toDbRows,
  type ProfileRow,
} from "./repository";
import type { OnboardingValues } from "./schema";

const values: OnboardingValues = {
  capital: 10000,
  riskLevel: 2,
  goals: [
    { targetReturnPct: 12, deadline: "2099-06-30" },
    { targetReturnPct: 30, deadline: "2100-01-01" },
  ],
};

describe("toDbRows", () => {
  it("solo manda nivel y capital (la base deriva el drawdown) y metas como fracción", () => {
    expect(toDbRows("u1", values)).toEqual({
      profile: {
        user_id: "u1",
        risk_level: 2,
        capital: 10000,
      },
      goals: [
        { target_return: 0.12, deadline: "2099-06-30" },
        { target_return: 0.3, deadline: "2100-01-01" },
      ],
    });
  });
});

describe("profileToForm", () => {
  it("convierte un perfil de la base (numeric como texto) al estado del formulario", () => {
    const row: ProfileRow = {
      id: "p1",
      risk_level: 4,
      capital: "2500.50",
      max_drawdown: "0.3",
      goals: [{ target_return: "0.07", deadline: "2099-01-01" }],
    };
    expect(profileToForm(row)).toEqual({
      capital: "2500.5",
      riskLevel: "4",
      goals: [{ targetReturnPct: "7", deadline: "2099-01-01" }],
    });
  });

  it("deja una meta vacía si el perfil no tiene metas", () => {
    const form = profileToForm({ id: "p1", risk_level: 3, capital: 500, goals: null });
    expect(form.goals).toEqual([{ targetReturnPct: "", deadline: "" }]);
  });

  it("usa el nivel 3 si el perfil trae un nivel fuera de 1..5", () => {
    expect(profileToForm({ id: "p1", risk_level: 9, capital: 500 }).riskLevel).toBe("3");
  });
});

describe("fetchProfile", () => {
  it("consulta el perfil del usuario con sus metas", async () => {
    const row = { id: "p1" };
    const fake = createFakeSupabase(() => ({ data: row }));
    await expect(fetchProfile(fake.client, "u1")).resolves.toBe(row);
    const [query] = fake.queries;
    expect(query.table).toBe("profiles");
    expect(query.calls.map((c) => c.method)).toEqual(["select", "eq", "maybeSingle"]);
    expect(query.calls[0].args[0]).toContain("goals(target_return, deadline)");
    expect(query.calls[0].args[0]).not.toMatch(/horizon|instruments/);
    expect(query.calls[1].args).toEqual(["user_id", "u1"]);
  });

  it("devuelve null si no hay perfil", async () => {
    const fake = createFakeSupabase(() => ({ data: null }));
    await expect(fetchProfile(fake.client, "u1")).resolves.toBeNull();
  });

  it("lanza OnboardingSaveError si falla la lectura", async () => {
    const fake = createFakeSupabase(() => ({ error: { message: "boom" } }));
    await expect(fetchProfile(fake.client, "u1")).rejects.toBeInstanceOf(OnboardingSaveError);
  });
});

describe("saveOnboarding", () => {
  it("hace upsert del perfil por user_id y reemplaza las metas", async () => {
    const fake = createFakeSupabase((q) =>
      q.table === "profiles" ? { data: { id: "p1" } } : { data: null },
    );
    await expect(saveOnboarding(fake.client, "u1", values)).resolves.toEqual({ profileId: "p1" });

    const [profile, del, insert] = fake.queries;
    expect(profile.op).toBe("upsert");
    expect(profile.calls[0].args).toEqual([toDbRows("u1", values).profile, { onConflict: "user_id" }]);

    expect(del.table).toBe("goals");
    expect(del.op).toBe("delete");
    expect(del.calls[1].args).toEqual(["profile_id", "p1"]);

    expect(insert.op).toBe("insert");
    expect(insert.calls[0].args[0]).toEqual([
      { target_return: 0.12, deadline: "2099-06-30", profile_id: "p1" },
      { target_return: 0.3, deadline: "2100-01-01", profile_id: "p1" },
    ]);
  });

  it.each([
    ["upsert", "No se pudo guardar tu perfil."],
    ["delete", "No se pudieron actualizar tus metas."],
    ["insert", "No se pudieron guardar tus metas."],
  ])("propaga el error de %s con un mensaje en español", async (failingOp, message) => {
    const fake = createFakeSupabase((q) => {
      if (q.op === failingOp) return { error: { message: "rls" } };
      return q.table === "profiles" ? { data: { id: "p1" } } : { data: null };
    });
    const promise = saveOnboarding(fake.client, "u1", values);
    await expect(promise).rejects.toBeInstanceOf(OnboardingSaveError);
    await expect(promise).rejects.toThrow(message);
  });
});
