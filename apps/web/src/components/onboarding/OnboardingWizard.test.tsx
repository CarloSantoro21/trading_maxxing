import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { createFakeSupabase, type Responder } from "@/test/fakeSupabase";
import { OnboardingWizard } from "./OnboardingWizard";

const ok: Responder = (q) => (q.table === "profiles" ? { data: { id: "p1" } } : { data: null });

function setup(respond: Responder = ok) {
  const fake = createFakeSupabase(respond);
  const onSaved = vi.fn();
  const user = userEvent.setup();
  render(<OnboardingWizard supabase={fake.client} userId="user-1" onSaved={onSaved} />);
  const next = () => user.click(screen.getByRole("button", { name: "Siguiente" }));
  return { fake, onSaved, user, next };
}

async function fillAll({ user, next }: ReturnType<typeof setup>) {
  await user.type(screen.getByLabelText("Capital inicial (USDT)"), "10000");
  await next();
  await user.click(screen.getByRole("radio", { name: /4 · Decidido/ }));
  await next();
  await user.type(screen.getByLabelText("Retorno objetivo (%) · meta 1"), "12");
  fireEvent.change(screen.getByLabelText("Fecha límite · meta 1"), { target: { value: "2099-12-31" } });
}

describe("OnboardingWizard", () => {
  it("tiene tres pasos y no pide horizonte, drawdown ni instrumentos", () => {
    setup();
    const steps = within(screen.getByRole("navigation", { name: "Progreso del onboarding" })).getAllByRole(
      "listitem",
    );
    expect(steps.map((s) => s.textContent)).toEqual([
      "Paso 1Capital",
      "Paso 2Nivel de riesgo",
      "Paso 3Metas",
    ]);
    expect(screen.queryByText(/horizonte/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("no avanza si el capital falta o es menor a 500 USDT", async () => {
    const { user, next } = setup();
    await next();
    expect(screen.getByRole("heading", { name: "Capital" })).toBeInTheDocument();
    expect(screen.getByText("Escribe el capital inicial.")).toBeInTheDocument();
    expect(screen.getByLabelText("Capital inicial (USDT)")).toHaveAttribute("aria-invalid", "true");

    await user.type(screen.getByLabelText("Capital inicial (USDT)"), "499");
    await next();
    expect(screen.getByText("El capital mínimo es de 500 USDT.")).toBeInTheDocument();
  });

  it("explica cada nivel con la caída máxima en dólares para el capital", async () => {
    const { user, next } = setup();
    await user.type(screen.getByLabelText("Capital inicial (USDT)"), "10000");
    await next();
    expect(screen.getByRole("heading", { name: "Nivel de riesgo" })).toHaveFocus();

    // Cada opción muestra su caída máxima en % y en USDT.
    expect(screen.getByRole("radio", { name: /1 · Conservador.*10 % \(1,000 USDT\)/ })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /5 · Agresivo.*40 % \(4,000 USDT\)/ })).toBeInTheDocument();

    // El 3 viene elegido por defecto y se explica en lenguaje simple.
    expect(screen.getByRole("radio", { name: /3 · Moderado/ })).toBeChecked();
    const explanation = () => screen.getByRole("heading", { name: /Nivel \d · / }).closest("section")!;
    expect(explanation()).toHaveTextContent("Con tus 10,000 USDT");
    expect(explanation()).toHaveTextContent("2,000 USDT (20 %)");
    expect(explanation()).toHaveTextContent("100 USDT (1 %)");
    expect(explanation()).toHaveTextContent("5,000 USDT (50 %)");

    await user.click(screen.getByRole("radio", { name: /1 · Conservador/ }));
    expect(explanation()).toHaveTextContent("Nivel 1 · Conservador");
    expect(explanation()).toHaveTextContent("1,000 USDT (10 %)");
    expect(explanation()).toHaveTextContent("25 USDT (0.25 %)");
  });

  it("guarda solo capital y nivel en el perfil, y las metas", async () => {
    const ctx = setup();
    await fillAll(ctx);
    const summary = screen.getByRole("heading", { name: "Resumen" }).closest("section")!;
    expect(summary).toHaveTextContent("10,000 USDT");
    expect(summary).toHaveTextContent("4 · Decidido");
    expect(summary).toHaveTextContent("30 % (3,000 USDT)");
    expect(summary).toHaveTextContent("BTC/USDT y ETH/USDT");
    await ctx.user.click(screen.getByRole("button", { name: "Guardar perfil" }));

    expect(await screen.findByRole("heading", { name: "¡Perfil guardado!" })).toBeInTheDocument();
    expect(ctx.onSaved).toHaveBeenCalledWith("p1");
    const [profile, , goals] = ctx.fake.queries;
    expect(profile.calls[0].args[0]).toEqual({ user_id: "user-1", risk_level: 4, capital: 10000 });
    expect(goals.calls[0].args[0]).toEqual([
      { target_return: 0.12, deadline: "2099-12-31", profile_id: "p1" },
    ]);

    await ctx.user.click(screen.getByRole("button", { name: "Editar de nuevo" }));
    expect(screen.getByRole("button", { name: "Guardar perfil" })).toBeInTheDocument();
  });

  it("permite agregar y quitar metas y valida cada una", async () => {
    const ctx = setup();
    await fillAll(ctx);
    await ctx.user.click(screen.getByRole("button", { name: "Agregar meta" }));
    await ctx.user.click(screen.getByRole("button", { name: "Guardar perfil" }));
    expect(screen.getByText("Escribe el retorno objetivo.")).toBeInTheDocument();
    expect(screen.getByText("Elige una fecha límite.")).toBeInTheDocument();
    expect(ctx.fake.queries).toHaveLength(0);

    await ctx.user.click(screen.getByRole("button", { name: "Quitar meta 2" }));
    expect(screen.queryByLabelText("Retorno objetivo (%) · meta 2")).not.toBeInTheDocument();
  });

  it("muestra el error de Supabase si falla el guardado (p. ej. RLS)", async () => {
    const ctx = setup(() => ({ error: { message: "new row violates row-level security policy" } }));
    await fillAll(ctx);
    await ctx.user.click(screen.getByRole("button", { name: "Guardar perfil" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("No se pudo guardar tu perfil.");
    expect(ctx.onSaved).not.toHaveBeenCalled();
  });

  it("Atrás regresa al paso anterior conservando lo capturado", async () => {
    const { user, next } = setup();
    await user.type(screen.getByLabelText("Capital inicial (USDT)"), "750");
    await next();
    await user.click(screen.getByRole("button", { name: "Atrás" }));
    expect(screen.getByLabelText("Capital inicial (USDT)")).toHaveValue(750);
    await waitFor(() => expect(screen.getByRole("button", { name: "Atrás" })).toBeDisabled());
  });

  it("se prellena con un perfil existente", async () => {
    const fake = createFakeSupabase();
    const user = userEvent.setup();
    render(
      <OnboardingWizard
        supabase={fake.client}
        userId="user-1"
        initial={{ capital: "2000", riskLevel: "1", goals: [{ targetReturnPct: "8", deadline: "2099-01-01" }] }}
      />,
    );
    expect(screen.getByLabelText("Capital inicial (USDT)")).toHaveValue(2000);
    await user.click(screen.getByRole("button", { name: "Siguiente" }));
    expect(screen.getByRole("radio", { name: /1 · Conservador.*\(200 USDT\)/ })).toBeChecked();
  });

  it("si el nivel guardado no es válido, pide elegir uno", async () => {
    const fake = createFakeSupabase();
    const user = userEvent.setup();
    render(
      <OnboardingWizard
        supabase={fake.client}
        userId="user-1"
        initial={{ capital: "", riskLevel: "", goals: [{ targetReturnPct: "", deadline: "" }] }}
      />,
    );
    await user.type(screen.getByLabelText("Capital inicial (USDT)"), "600");
    await user.click(screen.getByRole("button", { name: "Siguiente" }));
    expect(screen.queryByRole("heading", { name: /Nivel \d · / })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Siguiente" }));
    expect(screen.getByText(/nivel de riesgo/)).toBeInTheDocument();
  });
});
