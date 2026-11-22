import type { Page, Route } from "@playwright/test";

/**
 * Helpers para la variante *mocked* de los E2E (#28, #106): un enrutador que responde las
 * llamadas que el frontend hace a la API (`/api/*`, mismo origen; contrato en
 * docs/arquitectura/api.md). Con `page.route()` el navegador nunca llega al proxy de
 * `next dev` ni al Worker: la prueba corre sin `wrangler dev` ni Cloudflare Access.
 *
 * El estado (perfil guardado, bot creado, estado del bot) vive en memoria de la prueba y
 * se refleja en `GET /api/me` y `GET /api/dashboard`, que el dashboard vuelve a pedir por
 * polling. Todos los datos son inofensivos (no hay dinero real).
 */

export const MOCK_USER_ID = "11111111-1111-1111-1111-111111111111";
export const MOCK_PROFILE_ID = "22222222-2222-2222-2222-222222222222";
export const MOCK_BOT_ID = "33333333-3333-3333-3333-333333333333";
export const MOCK_TRADE_ID = "44444444-4444-4444-4444-444444444444";
export const MOCK_EMAIL = "demo@trading-maxxing.local";

type BotStatus = "stopped" | "running" | "paused" | "killed";

export interface MockState {
  profileSaved: boolean;
  botCreated: boolean;
  botStatus: BotStatus;
  /** Llamadas recibidas, como "MÉTODO /ruta" (sin `/api` ni query). */
  calls: string[];
}

function freshState(): MockState {
  return { profileSaved: false, botCreated: false, botStatus: "stopped", calls: [] };
}

function json(body: unknown, status = 200) {
  return {
    status,
    contentType: "application/json",
    body: body === undefined ? "" : JSON.stringify(body),
  };
}

function profile() {
  const now = new Date().toISOString();
  return {
    id: MOCK_PROFILE_ID,
    user_id: MOCK_USER_ID,
    horizon: "medium",
    risk_level: 3,
    capital: 1000,
    max_drawdown: 0.2,
    instruments: ["BTCUSDT", "ETHUSDT"],
    created_at: now,
    updated_at: now,
  };
}

function bot(status: BotStatus) {
  return {
    id: MOCK_BOT_ID,
    profile_id: MOCK_PROFILE_ID,
    name: "Mi bot",
    status,
    started_at: status === "running" ? new Date().toISOString() : null,
    stopped_at: status === "stopped" ? new Date().toISOString() : null,
  };
}

function trade() {
  return {
    id: MOCK_TRADE_ID,
    bot_id: MOCK_BOT_ID,
    symbol: "BTCUSDT",
    side: "buy" as const,
    quantity: "0.01",
    entry_price: "50000",
    exit_price: null,
    stop_loss: "48000",
    take_profit: "55000",
    fees: "0.5",
    pnl: null,
    rationale: {
      sleeve: "momentum",
      signal: { label: "momentum", confidence: 0.72 },
      simulation: { allow: true },
    },
    opened_at: new Date().toISOString(),
    closed_at: null,
  };
}

function snapshots() {
  const now = Date.now();
  return [
    { bot_id: MOCK_BOT_ID, ts: new Date(now - 60_000).toISOString(), equity: "1000", cash: "1000", positions_value: "0", drawdown: "0" },
    { bot_id: MOCK_BOT_ID, ts: new Date(now).toISOString(), equity: "1005", cash: "505", positions_value: "500", drawdown: "0" },
  ];
}

/** `GET /api/dashboard` según el estado de la prueba. */
function dashboard(state: MockState) {
  const p = profile();
  const running = state.botCreated && state.botStatus === "running";
  return {
    profile: state.profileSaved ? { id: p.id, capital: p.capital, risk_level: p.risk_level, max_drawdown: p.max_drawdown } : null,
    bots: state.botCreated ? [bot(state.botStatus)] : [],
    snapshots: state.botCreated ? snapshots() : [],
    recentTrades: running ? [trade()] : [],
    openTrades: running ? [trade()] : [],
    closed: {},
  };
}

/**
 * Instala el interceptor de `/api/*`. Un solo handler despacha por método y ruta; lo
 * que no conoce responde 404 con `{detail}` como la API real.
 */
export async function installMocks(page: Page): Promise<MockState> {
  const state = freshState();

  await page.route((url) => url.pathname.startsWith("/api/"), async (route: Route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname.replace(/^\/api/, "");
    const method = req.method();
    const key = `${method} ${path}`;
    state.calls.push(key);
    if (process.env.E2E_DEBUG_ROUTES) console.log(`[E2E] ${key}`);

    switch (key) {
      case "GET /me":
        return route.fulfill(
          json({ id: MOCK_USER_ID, email: MOCK_EMAIL, role: "member", profile: state.profileSaved ? profile() : null }),
        );
      case "GET /engine/status":
        return route.fulfill(
          json({ online: true, last_heartbeat_at: new Date().toISOString(), stale_after_seconds: 120, components: [] }),
        );
      case "GET /onboarding":
        return route.fulfill(
          json(
            state.profileSaved
              ? { id: MOCK_PROFILE_ID, risk_level: 3, capital: 1000, max_drawdown: 0.2, goals: [{ target_return: 0.15, deadline: "2099-01-01" }] }
              : null,
          ),
        );
      case "PUT /onboarding":
        state.profileSaved = true;
        return route.fulfill(json({ profileId: MOCK_PROFILE_ID, profile_id: MOCK_PROFILE_ID }));
      case "GET /dashboard":
        return route.fulfill(json(dashboard(state)));
      case "POST /bots":
        state.botCreated = true;
        state.botStatus = "stopped";
        return route.fulfill(json(bot("stopped"), 201));
      case `POST /bots/${MOCK_BOT_ID}/start`:
        state.botStatus = "running";
        return route.fulfill(json({ bot: bot("running"), command: "start", command_id: "cmd-1" }));
      case `POST /bots/${MOCK_BOT_ID}/stop`:
        state.botStatus = "stopped";
        return route.fulfill(json({ bot: bot("stopped"), command: "stop", command_id: "cmd-2" }));
      case `POST /bots/${MOCK_BOT_ID}/kill`:
        state.botStatus = "killed";
        return route.fulfill(json({ bot: bot("killed"), command: "kill", command_id: "cmd-3" }));
      case "GET /strategies/overview":
        return route.fulfill(json({ strategies: [], transitions: [], equity: [], isAdmin: false }));
      default:
        return route.fulfill(json({ detail: `sin mock para ${key}` }, 404));
    }
  });

  return state;
}
