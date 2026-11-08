import type { Session } from "@supabase/supabase-js";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "@/components/auth/AuthProvider";
import { AppShell } from "@/components/nav/AppShell";
import { mockApi, type ApiHandler } from "@/test/api";
import { createFakeSupabase, type Responder } from "@/test/fakeSupabase";
import { StrategiesView } from "./StrategiesView";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/strategies",
  useSearchParams: () => new URLSearchParams(),
}));

const labReport = {
  montecarlo: {
    horizon_days: 365,
    final_return: { p5: -0.1, p50: 0.1, p95: 0.3 },
    max_drawdown: { p95: 0.2, p99: 0.25 },
  },
  aggregate: { sharpe: 1.25, cagr: 0.15, max_drawdown: 0.18, trades: 42, start: "2022-01-01", end: "2025-12-31" },
  approval: { approved: true, checks: [{ name: "sharpe", passed: true, reason: "Sharpe OOS ≥ 1" }, { name: "dd", passed: false, reason: "Caída alta" }] },
};

function strategy(over: Record<string, unknown> = {}) {
  return {
    id: "s1",
    name: "trend",
    state: "shadow",
    params: {},
    lab_report: labReport,
    live_metrics: { equity: 1020, total_return: 0.02, shadow: { failures: 1 } },
    state_changed_at: "2026-10-20T00:00:00Z",
    shadow_days_clean: 8,
    ...over,
  };
}

const transitions = [
  { id: 1, strategy_id: "s1", from_state: "lab", to_state: "shadow", actor: "system", reason: "Aprobó el laboratorio", created_at: "2026-10-20T00:00:00Z" },
  { id: 2, strategy_id: "otra", from_state: "lab", to_state: "retired", actor: "system", reason: "no", created_at: "2026-10-20T00:00:00Z" },
];

const equity = [
  { strategy_id: "s1", started_at: "2026-10-20T00:00:00Z", day: "2026-10-20", equity: "1000", drawdown: "0" },
  { strategy_id: "s1", started_at: "2026-10-20T00:00:00Z", day: "2026-10-28", equity: "1003", drawdown: "0.01" },
];

function responder({ admin = false, strategies = [strategy()] }: { admin?: boolean; strategies?: unknown[] } = {}): Responder {
  return (q) => {
    if (q.table === "strategies") return { data: strategies };
    if (q.table === "strategy_transitions") return { data: transitions };
    if (q.table === "reference_equity") return { data: equity };
    if (q.table === "app_roles") return { data: admin ? { role: "admin" } : { role: "member" } };
    return { data: null };
  };
}

function renderView(respond: Responder, api?: ApiHandler, refreshDelayMs = 0) {
  const fake = createFakeSupabase(respond);
  fake.auth.getSession.mockResolvedValue({
    data: { session: { user: { id: "user-1" }, access_token: "jwt" } as unknown as Session },
    error: null,
  });
  const apiMock = mockApi(api);
  render(
    <AuthProvider client={fake.client}>
      <AppShell>
        <StrategiesView refreshDelayMs={refreshDelayMs} />
      </AppShell>
    </AuthProvider>,
  );
  return { fake, api: apiMock };
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("StrategiesView", () => {
  it("muestra estado, días en shadow, banda del MC, reporte del lab e historial", async () => {
    renderView(responder());
    expect(screen.getByRole("status")).toHaveTextContent("Cargando estrategias…");
    const card = await screen.findByRole("article");
    expect(within(card).getByText("Shadow")).toBeInTheDocument();
    expect(within(card).getByText("8.0")).toBeInTheDocument();
    expect(within(card).getByText(/1 falla en esta corrida/)).toBeInTheDocument();
    expect(within(card).getByRole("img", { name: /contra la banda del Monte Carlo/ })).toBeInTheDocument();
    expect(within(card).getByText("Dentro de la banda esperada por el laboratorio.")).toBeInTheDocument();
    expect(within(card).getByText("1,020")).toBeInTheDocument();
    expect(within(card).getByText("20.0 % / 25.0 %")).toBeInTheDocument();
    expect(within(card).getByText(/Aprobada por los criterios \(walk-forward 2022–2025\)/)).toBeInTheDocument();
    expect(within(card).getByText("1.25")).toBeInTheDocument();
    expect(within(card).getByText("Caída alta")).toBeInTheDocument();
    expect(within(card).getByText("Historial de estados (1)")).toBeInTheDocument();
    expect(within(card).getByText("Aprobó el laboratorio")).toBeInTheDocument();
    expect(within(card).getByText(/Sistema \(automático\)/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Estrategias" })).toHaveAttribute("aria-current", "page");
  });

  it("un member no ve los botones de promover ni retirar", async () => {
    renderView(responder());
    await screen.findByRole("article");
    expect(screen.queryByRole("button", { name: "Promover a activa" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retirar" })).not.toBeInTheDocument();
    expect(screen.queryByText("Admin")).not.toBeInTheDocument();
  });

  it("el admin promueve con confirmación dentro de la página y recarga", async () => {
    const { api, fake } = renderView(responder({ admin: true }), () => ({
      body: { from_state: "shadow", to_state: "active", actor: "user-1", reason: "8 días en shadow sin fallas" },
    }));
    await screen.findByRole("article");
    expect(screen.getByText("Admin")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Promover a activa" }));
    const dialog = screen.getByRole("dialog", { name: "¿Promover Tendencia a activa?" });
    expect(within(dialog).getByLabelText("Motivo")).toHaveValue("8 días en shadow sin fallas");
    const before = fake.queries.length;
    await userEvent.click(within(dialog).getByRole("button", { name: "Sí, promover" }));
    expect(await screen.findByText("Tendencia pasó de Shadow a Activa.")).toBeInTheDocument();
    expect(api.calls[0]).toMatchObject({
      method: "POST",
      path: "/strategies/trend/promote",
      body: { reason: "8 días en shadow sin fallas" },
    });
    expect(api.calls[0].headers.Authorization).toBe("Bearer jwt");
    await waitFor(() => expect(fake.queries.length).toBeGreaterThan(before));
  });

  it("con menos de 7 días limpios el botón de promover queda deshabilitado", async () => {
    renderView(responder({ admin: true, strategies: [strategy({ shadow_days_clean: 3 })] }));
    await screen.findByRole("article");
    expect(screen.getByRole("button", { name: "Promover a activa" })).toBeDisabled();
    expect(screen.getByText("Lleva 3.0 de 7 días en shadow sin fallas.")).toBeInTheDocument();
  });

  it("retirar exige un motivo y muestra el 403 de la API", async () => {
    const { api } = renderView(responder({ admin: true, strategies: [strategy({ state: "active" })] }), () => ({
      status: 403,
      body: { detail: "Solo un admin puede retirar estrategias" },
    }));
    await screen.findByRole("article");
    expect(screen.queryByRole("button", { name: "Promover a activa" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retirar" }));
    const dialog = screen.getByRole("dialog", { name: "¿Retirar Tendencia?" });
    const confirm = within(dialog).getByRole("button", { name: "Sí, retirar" });
    expect(confirm).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText("Motivo"), "  Se degradó  ");
    await userEvent.click(confirm);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Solo un admin puede retirar estrategias");
    expect(api.calls[0]).toMatchObject({ path: "/strategies/trend/retire", body: { reason: "Se degradó" } });
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancelar" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("sin banda ni curva ni reporte lo explica", async () => {
    renderView(
      responder({
        strategies: [strategy({ id: "s9", state: "lab", lab_report: null, live_metrics: {}, shadow_days_clean: null })],
      }),
    );
    const card = await screen.findByRole("article");
    expect(within(card).getByText("Sin curva de referencia")).toBeInTheDocument();
    expect(within(card).getByText("Todavía no pasa por el laboratorio.")).toBeInTheDocument();
    expect(within(card).getByText("Sin transiciones registradas.")).toBeInTheDocument();
  });

  it("estado vacío y error con reintento", async () => {
    let fail = true;
    renderView((q) => (fail ? { error: { message: "x" } } : responder({ strategies: [] })(q)));
    expect(await screen.findByRole("alert")).toHaveTextContent("No se pudo leer las estrategias.");
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByText("No hay estrategias registradas")).toBeInTheDocument();
  });

  it("vuelve a leer cuando Realtime avisa de un cambio", async () => {
    let state = "shadow";
    const { fake } = renderView((q) =>
      q.table === "strategies" ? { data: [strategy({ state })] } : responder()(q),
    );
    await screen.findByRole("article");
    expect(screen.getByText("En vivo")).toBeInTheDocument();
    state = "active";
    act(() => {
      fake.emitRealtime("strategies", "UPDATE", {});
      fake.emitRealtime("reference_equity", "INSERT", {});
    });
    expect(await screen.findByText("Activa")).toBeInTheDocument();
  });
});
