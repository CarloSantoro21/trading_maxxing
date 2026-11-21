import type { Page, Route } from "@playwright/test";

/**
 * Helpers para la variante *mocked* de los E2E (#28): un enrutador que responde las
 * llamadas que el frontend hace al cliente de Supabase (REST + Auth) y a la API de bots
 * (`NEXT_PUBLIC_API_URL`). El Realtime de Supabase va por WebSocket; en la *mocked* lo
 * dejamos caer: el dashboard muestra una insignia "en vivo" cuando conecta, pero la carga
 * inicial funciona igual sin ella.
 *
 * Playwright evalúa los interceptores en orden inverso (el último registrado gana), así
 * que los catch-all se registran primero y las rutas específicas al final.
 *
 * Los hosts se derivan de las variables públicas que el test inyecta antes de arrancar
 * el servidor de Next. Todos los datos que devolvemos son inofensivos (no hay dinero real)
 * y solo sirven para verificar que la UI encadena los pasos del flujo.
 */

export const MOCK_SUPABASE_HOST = "127.0.0.1:54399";
export const MOCK_API_HOST = "127.0.0.1:8099";
export const MOCK_SUPABASE_URL = `http://${MOCK_SUPABASE_HOST}`;
export const MOCK_API_URL = `http://${MOCK_API_HOST}`;

export const MOCK_USER_ID = "11111111-1111-1111-1111-111111111111";
export const MOCK_PROFILE_ID = "22222222-2222-2222-2222-222222222222";
export const MOCK_BOT_ID = "33333333-3333-3333-3333-333333333333";
export const MOCK_TRADE_ID = "44444444-4444-4444-4444-444444444444";

interface MockState {
  profileSaved: boolean;
  botCreated: boolean;
  botStatus: "stopped" | "running" | "paused" | "killed";
  loggedIn: boolean;
}

function freshState(): MockState {
  return { profileSaved: false, botCreated: false, botStatus: "stopped", loggedIn: false };
}

function session() {
  return {
    access_token: "test-access-token",
    token_type: "bearer",
    expires_in: 3600,
    expires_at: Math.floor(Date.now() / 1000) + 3600,
    refresh_token: "test-refresh-token",
    user: {
      id: MOCK_USER_ID,
      aud: "authenticated",
      role: "authenticated",
      email: "demo@trading-maxxing.local",
      app_metadata: { provider: "email" },
      user_metadata: {},
      created_at: new Date().toISOString(),
    },
  };
}

function json(body: unknown, status = 200) {
  return {
    status,
    contentType: "application/json",
    body: typeof body === "string" ? body : JSON.stringify(body),
  };
}

function profileRow() {
  return {
    id: MOCK_PROFILE_ID,
    user_id: MOCK_USER_ID,
    risk_level: 3,
    capital: 1000,
    max_drawdown: 0.2,
  };
}

function botRow(status: MockState["botStatus"]) {
  return {
    id: MOCK_BOT_ID,
    profile_id: MOCK_PROFILE_ID,
    name: "Mi bot",
    status,
    started_at: status === "running" ? new Date().toISOString() : null,
    stopped_at: status === "stopped" ? new Date().toISOString() : null,
  };
}

function tradeRow() {
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

function snapshotRows() {
  const now = Date.now();
  return [
    {
      bot_id: MOCK_BOT_ID,
      ts: new Date(now - 60_000).toISOString(),
      equity: "1000",
      cash: "1000",
      positions_value: "0",
      drawdown: "0",
    },
    {
      bot_id: MOCK_BOT_ID,
      ts: new Date(now).toISOString(),
      equity: "1005",
      cash: "505",
      positions_value: "500",
      drawdown: "0",
    },
  ];
}

/**
 * Instala todos los interceptores que necesita el flujo. Los handlers se registran en el
 * orden inverso al que se evalúan: primero los catch-all y, encima, los específicos
 * (Playwright corre el último registrado primero). El estado (perfil guardado, bot
 * creado, estado del bot) vive en memoria del test.
 */
export async function installMocks(page: Page): Promise<MockState> {
  const state = freshState();

  // Log opcional para depurar.
  if (process.env.E2E_DEBUG_ROUTES) {
    page.on("request", (req) => {
      const url = req.url();
      if (url.includes("/rest/v1/") || url.includes("/auth/v1/") || url.startsWith(MOCK_API_URL)) {
        console.log(`[E2E] ${req.method()} ${url}`);
      }
    });
  }

  // --- Catch-all primero (menor prioridad por LIFO) --------------------------------------

  // Cualquier otra ruta REST de Supabase: arreglo vacío.
  await page.route(/\/rest\/v1\/.*/, async (route) => {
    if (!route.request().url().startsWith(MOCK_SUPABASE_URL)) return route.fallback();
    await route.fulfill(json([]));
  });

  // Realtime: el cliente intenta abrir un WebSocket, lo abortamos para no colgar la prueba.
  await page.route(/\/realtime\/v1\/.*/, async (route) => {
    if (!route.request().url().startsWith(MOCK_SUPABASE_URL)) return route.fallback();
    await route.abort();
  });

  // --- Supabase Auth ----------------------------------------------------------------------
  await page.route(/\/auth\/v1\/token.*/, async (route: Route) => {
    if (!route.request().url().startsWith(MOCK_SUPABASE_URL)) return route.fallback();
    state.loggedIn = true;
    await route.fulfill(json(session()));
  });
  await page.route(/\/auth\/v1\/user.*/, async (route) => {
    if (!route.request().url().startsWith(MOCK_SUPABASE_URL)) return route.fallback();
    await route.fulfill(json(session().user));
  });
  await page.route(/\/auth\/v1\/logout.*/, async (route) => {
    if (!route.request().url().startsWith(MOCK_SUPABASE_URL)) return route.fallback();
    await route.fulfill(json({}, 204));
  });

  // --- Supabase REST: profiles ------------------------------------------------------------
  await page.route(/\/rest\/v1\/profiles(\?.*)?$/, async (route) => {
    if (!route.request().url().startsWith(MOCK_SUPABASE_URL)) return route.fallback();
    const req = route.request();
    const method = req.method();
    const accept = req.headers()["accept"] ?? "";
    const wantsSingle = accept.includes("application/vnd.pgrst.object+json");
    if (method === "GET") {
      if (!state.profileSaved) {
        if (wantsSingle) {
          return route.fulfill({
            status: 406,
            contentType: "application/json",
            body: JSON.stringify({
              code: "PGRST116",
              details: "Results contain 0 rows",
              hint: null,
              message: "JSON object requested, multiple (or no) rows returned",
            }),
          });
        }
        return route.fulfill(json([]));
      }
      const row = { ...profileRow(), goals: [{ target_return: 0.15, deadline: "2099-01-01" }] };
      return route.fulfill(json(wantsSingle ? row : [row]));
    }
    if (method === "POST" || method === "PATCH") {
      state.profileSaved = true;
      const body = wantsSingle ? profileRow() : [profileRow()];
      return route.fulfill(json(body, 201));
    }
    return route.fulfill(json([]));
  });

  // --- Supabase REST: goals ---------------------------------------------------------------
  await page.route(/\/rest\/v1\/goals(\?.*)?$/, async (route) => {
    if (!route.request().url().startsWith(MOCK_SUPABASE_URL)) return route.fallback();
    const method = route.request().method();
    await route.fulfill(json([], method === "POST" ? 201 : 200));
  });

  // --- Supabase REST: bots ----------------------------------------------------------------
  await page.route(/\/rest\/v1\/bots(\?.*)?$/, async (route) => {
    if (!route.request().url().startsWith(MOCK_SUPABASE_URL)) return route.fallback();
    const body = state.botCreated ? [botRow(state.botStatus)] : [];
    await route.fulfill(json(body));
  });

  // --- Supabase REST: equity_snapshots ----------------------------------------------------
  await page.route(/\/rest\/v1\/equity_snapshots(\?.*)?$/, async (route) => {
    if (!route.request().url().startsWith(MOCK_SUPABASE_URL)) return route.fallback();
    await route.fulfill(json(state.botCreated ? snapshotRows() : []));
  });

  // --- Supabase REST: trades --------------------------------------------------------------
  await page.route(/\/rest\/v1\/trades(\?.*)?$/, async (route) => {
    if (!route.request().url().startsWith(MOCK_SUPABASE_URL)) return route.fallback();
    const showTrade = state.botCreated && state.botStatus === "running";
    await route.fulfill(json(showTrade ? [tradeRow()] : []));
  });

  // --- API de bots (services/api) --------------------------------------------------------
  await page.route(/\/bots\/[^/]+\/start$/, async (route) => {
    if (!route.request().url().startsWith(MOCK_API_URL)) return route.fallback();
    state.botStatus = "running";
    await route.fulfill(
      json({ bot: botRow("running"), command: "start", command_id: "cmd-1" }),
    );
  });
  await page.route(/\/bots\/[^/]+\/stop$/, async (route) => {
    if (!route.request().url().startsWith(MOCK_API_URL)) return route.fallback();
    state.botStatus = "stopped";
    await route.fulfill(
      json({ bot: botRow("stopped"), command: "stop", command_id: "cmd-2" }),
    );
  });
  await page.route(/\/bots\/[^/]+\/kill$/, async (route) => {
    if (!route.request().url().startsWith(MOCK_API_URL)) return route.fallback();
    state.botStatus = "killed";
    await route.fulfill(
      json({ bot: botRow("killed"), command: "kill", command_id: "cmd-3" }),
    );
  });
  await page.route(/\/bots$/, async (route) => {
    if (!route.request().url().startsWith(MOCK_API_URL)) return route.fallback();
    const req = route.request();
    if (req.method() === "POST") {
      state.botCreated = true;
      state.botStatus = "stopped";
      return route.fulfill(json(botRow("stopped"), 201));
    }
    return route.fulfill(json({}, 404));
  });

  return state;
}
