import type { Session } from "@supabase/supabase-js";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "@/components/auth/AuthProvider";
import { AppShell } from "@/components/nav/AppShell";
import { mockApi, type ApiHandler } from "@/test/api";
import { called, createFakeSupabase, type Responder } from "@/test/fakeSupabase";
import { DashboardView } from "./DashboardView";

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(),
}));

const profile = { id: "p1", capital: "1000", risk_level: 3, max_drawdown: "0.15" };
const bot = { id: "b1", profile_id: "p1", name: "Mi bot", status: "stopped", started_at: null, stopped_at: null };
const trade = {
  id: "t1",
  bot_id: "b1",
  symbol: "BTCUSDT",
  side: "buy",
  quantity: "0.01",
  entry_price: "50000",
  exit_price: null,
  stop_loss: "48000",
  take_profit: "55000",
  fees: "0.5",
  pnl: null,
  rationale: {
    sleeve: "trend",
    signals: [{ symbol: "BTCUSDT", direction: "long", confidence: 0.8, timeframe: "1d", source: "trend" }],
    risk: { approved: true, reasons: [] },
  },
  opened_at: "2026-11-01T10:00:00Z",
  closed_at: null,
};
const snapshots = [
  { bot_id: "b1", ts: "2026-11-02T00:00:00Z", equity: "1050" },
  { bot_id: "b1", ts: "2026-11-01T00:00:00Z", equity: "1000" },
];

const fullData: Responder = (q) => {
  if (q.table === "profiles") return { data: profile };
  if (q.table === "bots") return { data: [bot] };
  if (q.table === "equity_snapshots") return { data: snapshots };
  if (called(q, "not")) return { data: [] };
  return { data: [trade] };
};

function renderDashboard(respond: Responder, api?: ApiHandler) {
  const fake = createFakeSupabase(respond);
  fake.auth.getSession.mockResolvedValue({
    data: { session: { user: { id: "user-1", email: "a@b.mx" }, access_token: "jwt" } as unknown as Session },
    error: null,
  });
  const apiMock = mockApi(api);
  const utils = render(
    <AuthProvider client={fake.client}>
      <AppShell>
        <DashboardView />
      </AppShell>
    </AuthProvider>,
  );
  return { fake, api: apiMock, ...utils };
}

beforeEach(() => replace.mockReset());
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("DashboardView", () => {
  it("muestra la carga y luego equity, P&L, posiciones y operaciones con su rationale", async () => {
    renderDashboard(fullData);
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(await screen.findByText("1,050.00 USDT")).toBeInTheDocument();
    expect(screen.getByText("+50.00 USDT")).toBeInTheDocument();
    expect(screen.getByText("+5.00 %")).toBeInTheDocument();
    expect(screen.getByText(/tu límite \(nivel 3\): 15 %/)).toBeInTheDocument();
    expect(screen.getByText("En vivo")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: /Posiciones abiertas/ })).toBeInTheDocument();
    expect(screen.getAllByText("BTC/USDT").length).toBeGreaterThan(0);
    expect(screen.getByText("Abierta")).toBeInTheDocument();
    expect(screen.getByText(/recomienda comprar BTC\/USDT con 80 % de confianza/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Dashboard" })).toHaveAttribute("aria-current", "page");
  });

  it("muestra el error y reintenta", async () => {
    let fail = true;
    renderDashboard((q) => {
      if (fail) return { error: { message: "x" } };
      return fullData(q);
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("No se pudo leer tu perfil.");
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByText("1,050.00 USDT")).toBeInTheDocument();
  });

  it("sin perfil manda al onboarding", async () => {
    renderDashboard(() => ({ data: null }));
    expect(await screen.findByText("Todavía no configuras tu perfil")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Configurar mi perfil" })).toHaveAttribute("href", "/onboarding");
  });

  it("sin bot ofrece crearlo con la API y muestra los estados vacíos", async () => {
    const { api } = renderDashboard(
      (q) => (q.table === "profiles" ? { data: profile } : { data: [] }),
      () => ({ status: 201, body: bot }),
    );
    expect(await screen.findByText("Aún no tienes bot")).toBeInTheDocument();
    expect(screen.getByText("Aún no hay curva de equity")).toBeInTheDocument();
    expect(screen.getByText("Sin posiciones todavía")).toBeInTheDocument();
    expect(screen.getByText("Aún no hay operaciones")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Crear mi bot" }));
    expect(await screen.findByRole("button", { name: "Arrancar" })).toBeEnabled();
    expect(api.calls[0]).toMatchObject({ method: "POST", path: "/bots", body: { name: "Mi bot" } });
    expect(api.calls[0].headers.Authorization).toBe("Bearer jwt");
  });

  it("muestra el error de la API al crear el bot", async () => {
    renderDashboard(
      (q) => (q.table === "profiles" ? { data: profile } : { data: [] }),
      () => ({ status: 409, body: { detail: "Ya tienes un bot" } }),
    );
    await userEvent.click(await screen.findByRole("button", { name: "Crear mi bot" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Ya tienes un bot");
  });

  it("aplica en vivo los trades, la equity y el estado del bot que llegan por Realtime", async () => {
    const { fake } = renderDashboard(fullData);
    await screen.findByText("1,050.00 USDT");
    expect(fake.listenerCount("trades")).toBe(1);
    act(() => {
      fake.emitRealtime("equity_snapshots", "INSERT", { bot_id: "b1", ts: "2026-11-03T00:00:00Z", equity: "1100" });
      fake.emitRealtime("trades", "UPDATE", { ...trade, closed_at: "2026-11-03T00:00:00Z", exit_price: "52000", pnl: "20" });
      fake.emitRealtime("bots", "UPDATE", { ...bot, status: "running", started_at: "2026-11-03T00:00:00Z" });
    });
    expect(screen.getByText("1,100.00 USDT")).toBeInTheDocument();
    expect(screen.getAllByText("+20.00 USDT").length).toBeGreaterThan(0);
    expect(screen.getByText("Operando")).toBeInTheDocument();
    act(() => fake.emitRealtime("bots", "DELETE", {}, { id: "b1" }));
    expect(screen.getByText("Aún no tienes bot")).toBeInTheDocument();
  });

  it("marca la conexión en vivo caída y se desuscribe al desmontar", async () => {
    const fake = createFakeSupabase(fullData);
    fake.setSubscribeStatus("CHANNEL_ERROR");
    fake.auth.getSession.mockResolvedValue({
      data: { session: { user: { id: "user-1" }, access_token: "jwt" } as unknown as Session },
      error: null,
    });
    const { unmount } = render(
      <AuthProvider client={fake.client}>
        <DashboardView />
      </AuthProvider>,
    );
    expect(await screen.findByText("1,050.00 USDT")).toBeInTheDocument();
    expect(screen.getByText("Sin conexión en vivo")).toBeInTheDocument();
    unmount();
    expect(fake.removeChannel).toHaveBeenCalled();
    expect(fake.listenerCount("trades")).toBe(0);
  });
});

describe("BotControls", () => {
  it("arranca y detiene el bot con la API", async () => {
    const { api } = renderDashboard(fullData, (call) => {
      const command = call.path.split("/").pop();
      const status = command === "start" ? "running" : "stopped";
      return { body: { bot: { ...bot, status, started_at: "2026-11-03T00:00:00Z" }, command, command_id: "c1" } };
    });
    await userEvent.click(await screen.findByRole("button", { name: "Arrancar" }));
    expect(await screen.findByText("Operando")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Arrancar" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Detener" }));
    await waitFor(() => expect(screen.getByText("Detenido")).toBeInTheDocument());
    expect(api.calls.map((c) => c.path)).toEqual(["/bots/b1/start", "/bots/b1/stop"]);
  });

  it("el paro de emergencia pide confirmación en la página y se puede cancelar", async () => {
    const { api } = renderDashboard(
      (q) => (q.table === "bots" ? { data: [{ ...bot, status: "running" }] } : fullData(q)),
      () => ({ body: { bot: { ...bot, status: "killed", stopped_at: "2026-11-03T00:00:00Z" }, command: "kill", command_id: "c" } }),
    );
    await userEvent.click(await screen.findByRole("button", { name: "Paro de emergencia" }));
    const dialog = screen.getByRole("dialog", { name: "¿Activar el paro de emergencia?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancelar" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(api.calls).toHaveLength(0);

    await userEvent.click(screen.getByRole("button", { name: "Paro de emergencia" }));
    await userEvent.click(screen.getByRole("button", { name: "Sí, vender todo y detener" }));
    expect(await screen.findByText(/Sus posiciones se vendieron a USDT/)).toBeInTheDocument();
    expect(api.calls[0]).toMatchObject({ path: "/bots/b1/kill", body: { reason: "paro de emergencia manual" } });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("muestra el error de la API dentro de la confirmación", async () => {
    renderDashboard(
      (q) => (q.table === "bots" ? { data: [{ ...bot, status: "running" }] } : fullData(q)),
      () => ({ status: 503, body: { detail: "Redis no responde" } }),
    );
    await userEvent.click(await screen.findByRole("button", { name: "Paro de emergencia" }));
    await userEvent.click(screen.getByRole("button", { name: "Sí, vender todo y detener" }));
    const dialog = screen.getByRole("dialog");
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Redis no responde");
  });

  it("explica un error al arrancar", async () => {
    renderDashboard(fullData, () => ({ status: 409, body: { detail: "Sin perfil" } }));
    await userEvent.click(await screen.findByRole("button", { name: "Arrancar" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Sin perfil");
  });
});
