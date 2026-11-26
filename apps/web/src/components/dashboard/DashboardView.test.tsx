import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { RequireAuth } from "@/components/auth/RequireAuth";
import { AppShell } from "@/components/nav/AppShell";
import { ENGINE_ONLINE, mockApi, type RouteHandler, type Routes } from "@/test/api";
import { renderWithApi } from "@/test/render";
import { DashboardView } from "./DashboardView";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(),
}));

const profile = { id: "p1", capital: 1000, risk_level: 3, max_drawdown: 0.15 };
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


function dashboard(over: Record<string, unknown> = {}) {
  return {
    profile,
    bots: [bot],
    snapshots,
    recentTrades: [trade],
    openTrades: [trade],
    closed: {},
    ...over,
  };
}

const DASHBOARD = "GET /dashboard";
const EMPTY = dashboard({ bots: [], snapshots: [], recentTrades: [], openTrades: [] });

function renderDashboard(routes: Routes, refreshMs = 60_000) {
  const api = mockApi(routes);
  const utils = renderWithApi(
    <RequireAuth>
      <AppShell>
        <DashboardView refreshMs={refreshMs} />
      </AppShell>
    </RequireAuth>,
  );
  return { api, ...utils };
}

describe("DashboardView", () => {
  it("muestra equity, P&L, posiciones y operaciones con su rationale", async () => {
    const { api } = renderDashboard({ [DASHBOARD]: dashboard() });
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
    expect(api.calls.some((c) => c.method === "GET" && c.path === "/dashboard")).toBe(true);
  });

  it("muestra el error y reintenta", async () => {
    let fail = true;
    renderDashboard({
      [DASHBOARD]: () => (fail ? { status: 500, body: { detail: "x" } } : { body: dashboard() }),
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("No se pudo cargar el dashboard.");
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByText("1,050.00 USDT")).toBeInTheDocument();
  });

  it("sin perfil manda al onboarding", async () => {
    renderDashboard({ [DASHBOARD]: { ...EMPTY, profile: null } });
    expect(await screen.findByText("Todavía no configuras tu perfil")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Configurar mi perfil" })).toHaveAttribute("href", "/onboarding");
  });

  it("sin bot ofrece crearlo con la API y muestra los estados vacíos", async () => {
    const { api } = renderDashboard({ [DASHBOARD]: EMPTY, "POST /bots": { status: 201, body: bot } });
    expect(await screen.findByText("Aún no tienes bot")).toBeInTheDocument();
    expect(screen.getByText("Aún no hay curva de equity")).toBeInTheDocument();
    expect(screen.getByText("Sin posiciones todavía")).toBeInTheDocument();
    expect(screen.getByText("Aún no hay operaciones")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Crear mi bot" }));
    expect(await screen.findByRole("button", { name: "Arrancar" })).toBeEnabled();
    const post = api.actions().find((c) => c.method === "POST");
    expect(post).toMatchObject({ method: "POST", path: "/bots", body: { name: "Mi bot" } });
    // Sin tokens: la cookie de Access viaja sola (mismo origen).
    expect(post?.headers.Authorization).toBeUndefined();
  });

  it("muestra el error de la API al crear el bot", async () => {
    renderDashboard({ [DASHBOARD]: EMPTY, "POST /bots": { status: 409, body: { detail: "Ya tienes un bot" } } });
    await userEvent.click(await screen.findByRole("button", { name: "Crear mi bot" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Ya tienes un bot");
  });

  it("vuelve a pedir el dashboard cada cierto tiempo (polling)", async () => {
    let data = dashboard();
    renderDashboard({ [DASHBOARD]: () => ({ body: data }) }, 50);
    await screen.findByText("1,050.00 USDT");
    data = dashboard({
      snapshots: [...snapshots, { bot_id: "b1", ts: "2026-11-03T00:00:00Z", equity: "1100" }],
      bots: [{ ...bot, status: "running", started_at: "2026-11-03T00:00:00Z" }],
      openTrades: [],
      recentTrades: [{ ...trade, closed_at: "2026-11-03T00:00:00Z", exit_price: "52000", pnl: "20" }],
      closed: { t1: { sleeve: "trend", pnl: 20 } },
    });
    expect(await screen.findByText("1,100.00 USDT", {}, { timeout: 2000 })).toBeInTheDocument();
    expect(screen.getAllByText("+20.00 USDT").length).toBeGreaterThan(0);
    expect(screen.getByText("Operando")).toBeInTheDocument();
  });
});

describe("BotControls", () => {
  const command: RouteHandler = (call) => {
    const name = call.path.split("/").pop();
    const status = name === "start" ? "running" : "stopped";
    return { body: { bot: { ...bot, status, started_at: "2026-11-03T00:00:00Z" }, command: name, command_id: "c1" } };
  };
  const running = dashboard({ bots: [{ ...bot, status: "running" }] });

  it("arranca y detiene el bot con la API", async () => {
    const { api } = renderDashboard({ [DASHBOARD]: dashboard(), "POST /bots/b1/start": command, "POST /bots/b1/stop": command });
    await userEvent.click(await screen.findByRole("button", { name: "Arrancar" }));
    expect(await screen.findByText("Operando")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Arrancar" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Detener" }));
    await waitFor(() => expect(screen.getByText("Detenido")).toBeInTheDocument());
    expect(api.actions().filter((c) => c.method === "POST").map((c) => c.path)).toEqual(["/bots/b1/start", "/bots/b1/stop"]);
  });

  it("el paro de emergencia pide confirmación en la página y se puede cancelar", async () => {
    const { api } = renderDashboard({
      [DASHBOARD]: running,
      "POST /bots/b1/kill": {
        body: { bot: { ...bot, status: "killed", stopped_at: "2026-11-03T00:00:00Z" }, command: "kill", command_id: "c" },
      },
    });
    const posts = () => api.actions().filter((c) => c.method === "POST");
    await userEvent.click(await screen.findByRole("button", { name: "Paro de emergencia" }));
    const dialog = screen.getByRole("dialog", { name: "¿Activar el paro de emergencia?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancelar" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(posts()).toHaveLength(0);

    await userEvent.click(screen.getByRole("button", { name: "Paro de emergencia" }));
    await userEvent.click(screen.getByRole("button", { name: "Sí, vender todo y detener" }));
    expect(await screen.findByText(/Sus posiciones se vendieron a USDT/)).toBeInTheDocument();
    expect(posts()[0]).toMatchObject({ path: "/bots/b1/kill", body: { reason: "paro de emergencia manual" } });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("muestra el error de la API dentro de la confirmación", async () => {
    renderDashboard({
      [DASHBOARD]: running,
      "POST /bots/b1/kill": { status: 500, body: { detail: "D1 no responde" } },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Paro de emergencia" }));
    await userEvent.click(screen.getByRole("button", { name: "Sí, vender todo y detener" }));
    const dialog = screen.getByRole("dialog");
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("D1 no responde");
  });

  it("explica un error al arrancar", async () => {
    renderDashboard({ [DASHBOARD]: dashboard(), "POST /bots/b1/start": { status: 409, body: { detail: "Sin perfil" } } });
    await userEvent.click(await screen.findByRole("button", { name: "Arrancar" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Sin perfil");
  });
});

describe("BotControls sin motor", () => {
  const OFFLINE = { "GET /engine/status": { ...ENGINE_ONLINE, online: false } };
  const heartbeat = (component: string, online: boolean) => ({
    component,
    status: "ok",
    version: null,
    host: null,
    started_at: null,
    last_seen_at: "2026-11-21T10:00:00.000Z",
    age_seconds: online ? 5 : 900,
    online,
  });
  const started: RouteHandler = () => ({
    body: { bot: { ...bot, status: "running", started_at: "2026-11-03T00:00:00Z" }, command: "start", command_id: "c1" },
  });

  it("con el bot encendido y el motor apagado dice Encendido (sin motor) y muestra el aviso", async () => {
    renderDashboard({ [DASHBOARD]: dashboard({ bots: [{ ...bot, status: "running" }] }), ...OFFLINE });
    expect(await screen.findByText("Encendido (sin motor)")).toBeInTheDocument();
    expect(screen.queryByText("Operando")).not.toBeInTheDocument();
    expect(screen.getByTestId("engine-down-banner")).toHaveTextContent("no va a operar: el motor no está encendido ni conectado");
  });

  it("con el motor en línea dice Operando y no muestra el aviso", async () => {
    renderDashboard({ [DASHBOARD]: dashboard({ bots: [{ ...bot, status: "running" }] }) });
    expect(await screen.findByText("Operando")).toBeInTheDocument();
    await screen.findByText("Motor en línea");
    expect(screen.queryByTestId("engine-down-banner")).not.toBeInTheDocument();
  });

  it("si solo falta el engine también avisa (el motor está incompleto)", async () => {
    renderDashboard({
      [DASHBOARD]: dashboard({ bots: [{ ...bot, status: "running" }] }),
      "GET /engine/status": { ...ENGINE_ONLINE, components: [heartbeat("agents", true), heartbeat("engine", false)] },
    });
    expect(await screen.findByTestId("engine-down-banner")).toHaveTextContent("falta engine");
    expect(screen.getByText("Encendido (sin motor)")).toBeInTheDocument();
  });

  it("al arrancar sin motor pide confirmación y se puede cancelar o encender de todos modos", async () => {
    const { api } = renderDashboard({ [DASHBOARD]: dashboard(), "POST /bots/b1/start": started, ...OFFLINE });
    const posts = () => api.actions().filter((c) => c.method === "POST");
    await screen.findByText("Motor desconectado");
    await userEvent.click(await screen.findByRole("button", { name: "Arrancar" }));
    const dialog = screen.getByRole("dialog", { name: "El motor está apagado: tu bot no va a hacer nada" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancelar" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(posts()).toHaveLength(0);

    await userEvent.click(screen.getByRole("button", { name: "Arrancar" }));
    await userEvent.click(screen.getByRole("button", { name: "Encender de todos modos" }));
    expect(await screen.findByText("Encendido (sin motor)")).toBeInTheDocument();
    expect(screen.getByTestId("engine-down-banner")).toBeInTheDocument();
    expect(posts().map((c) => c.path)).toEqual(["/bots/b1/start"]);
  });
});
