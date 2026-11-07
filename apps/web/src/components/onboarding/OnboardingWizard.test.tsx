import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
  await user.click(screen.getByRole("radio", { name: /Mediano plazo/ }));
  await next();
  await user.click(screen.getByRole("radio", { name: /4 · Agresivo/ }));
  const dd = screen.getByLabelText("Drawdown máximo (%)");
  await user.clear(dd);
  await user.type(dd, "25");
  await next();
  await user.click(screen.getByRole("checkbox", { name: "ETH/USDT" }));
  await next();
  await user.type(screen.getByLabelText("Retorno objetivo (%) · meta 1"), "12");
  fireEvent.change(screen.getByLabelText("Fecha límite · meta 1"), { target: { value: "2099-12-31" } });
}

describe("OnboardingWizard", () => {
  it("no avanza si el paso tiene errores y los anuncia en cada campo", async () => {
    const { next } = setup();
    await next();
    expect(screen.getByRole("heading", { name: "Capital y horizonte" })).toBeInTheDocument();
    expect(screen.getByText("Escribe el capital inicial.")).toBeInTheDocument();
    expect(screen.getByText("Elige un horizonte de inversión.")).toBeInTheDocument();
    expect(screen.getByLabelText("Capital inicial (USDT)")).toHaveAttribute("aria-invalid", "true");
  });

  it("valida el drawdown y los instrumentos en sus pasos", async () => {
    const { user, next } = setup();
    await user.type(screen.getByLabelText("Capital inicial (USDT)"), "500");
    await user.click(screen.getByRole("radio", { name: /Corto plazo/ }));
    await next();

    const dd = screen.getByLabelText("Drawdown máximo (%)");
    await user.clear(dd);
    await user.type(dd, "150");
    await next();
    expect(screen.getByText(/menor que 100 %/)).toBeInTheDocument();
    await user.clear(dd);
    await user.type(dd, "10");
    await next();

    await user.click(screen.getByRole("checkbox", { name: "BTC/USDT" }));
    await user.click(screen.getByRole("checkbox", { name: "ETH/USDT" }));
    await next();
    expect(screen.getByText("Elige al menos un instrumento.")).toBeInTheDocument();
  });

  it("guarda el perfil y las metas en Supabase con los valores convertidos", async () => {
    const ctx = setup();
    await fillAll(ctx);
    expect(screen.getByText("10000 USDT")).toBeInTheDocument();
    await ctx.user.click(screen.getByRole("button", { name: "Guardar perfil" }));

    expect(await screen.findByRole("heading", { name: "¡Perfil guardado!" })).toBeInTheDocument();
    expect(ctx.onSaved).toHaveBeenCalledWith("p1");
    const [profile, , goals] = ctx.fake.queries;
    expect(profile.calls[0].args[0]).toEqual({
      user_id: "user-1",
      horizon: "medium",
      risk_level: 4,
      capital: 10000,
      max_drawdown: 0.25,
      instruments: ["BTCUSDT"],
    });
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
    await user.click(screen.getByRole("radio", { name: /Largo plazo/ }));
    await next();
    expect(screen.getByRole("heading", { name: "Riesgo" })).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "Atrás" }));
    expect(screen.getByLabelText("Capital inicial (USDT)")).toHaveValue(750);
    await waitFor(() => expect(screen.getByRole("button", { name: "Atrás" })).toBeDisabled());
  });

  it("se prellena con un perfil existente", () => {
    const fake = createFakeSupabase();
    render(
      <OnboardingWizard
        supabase={fake.client}
        userId="user-1"
        initial={{
          capital: "2000",
          horizon: "long",
          riskLevel: "1",
          maxDrawdownPct: "5",
          instruments: ["ETHUSDT"],
          goals: [{ targetReturnPct: "8", deadline: "2099-01-01" }],
        }}
      />,
    );
    expect(screen.getByLabelText("Capital inicial (USDT)")).toHaveValue(2000);
    expect(screen.getByRole("radio", { name: /Largo plazo/ })).toBeChecked();
  });
});
