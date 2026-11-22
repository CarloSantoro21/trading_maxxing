import { expect, test, type APIRequestContext } from "@playwright/test";

/**
 * E2E contra el stack local REAL (#28): `make e2e-local` (o `make dev` y luego esta prueba con
 * `LOCAL_STACK=1`). Nada está mockeado: la web de `next dev` reenvía `/api/*` a `wrangler dev`
 * (Worker + D1 local con el seed) y el motor corre en docker compose con la estrategia de demo
 * `demo_pulse` (long/flat cada 2 min en velas de 1 min) y la ejecución simulada.
 *
 * Recorre el camino completo de una operación:
 *   web → POST /api/bots/{id}/start → tabla commands (D1) → agents (GET /internal/commands)
 *   → capa compartida (señal + SimulationGate) → BotRunner (allocator + RiskAgent)
 *   → tm:decisions (Redis) → engine (fill) → POST /internal/trades → D1 → GET /api/dashboard.
 *
 * Variables: `LOCAL_STACK=1` (si no, se omite), `E2E_BASE_URL` (web, por omisión
 * http://127.0.0.1:3000) y `E2E_SKIP_WEBSERVER=1` (la web ya corre). Tarda hasta ~6 minutos.
 */

const LOCAL = process.env.LOCAL_STACK === "1";
const TRADE_TIMEOUT_MS = 6 * 60_000;

interface Trade {
  id: string;
  symbol: string;
  side: string;
  opened_at: string;
  closed_at: string | null;
  rationale: { sleeve?: string; execution?: string } | null;
}

async function newTrades(request: APIRequestContext, since: number): Promise<Trade[]> {
  const res = await request.get("/api/trades?limit=50");
  if (!res.ok()) return [];
  const body = (await res.json()) as { items: Trade[] };
  return body.items.filter(
    (t) => Date.parse(t.opened_at) >= since || (t.closed_at !== null && Date.parse(t.closed_at) >= since),
  );
}

test.describe("stack local de punta a punta", () => {
  test.skip(!LOCAL, "Solo con LOCAL_STACK=1 (make e2e-local), contra make dev.");

  test("onboarding → arrancar bot → trade del motor → dashboard", async ({ page, request }) => {
    test.setTimeout(TRADE_TIMEOUT_MS + 3 * 60_000);
    // Un minuto de margen: la operación puede abrirse en el ciclo justo antes de pulsar Arrancar
    // si el bot ya estaba corriendo de una prueba anterior.
    const since = Date.now() - 60_000;

    // 1. La API y el motor responden (latido de agents y engine).
    const health = await request.get("/api/health");
    expect(health.ok(), "GET /api/health por el proxy de next dev").toBeTruthy();
    expect(await health.json()).toMatchObject({ service: "api", status: "ok", environment: "dev" });

    // 2. El sitio: onboarding si el usuario aún no tiene perfil, si no el dashboard.
    await page.goto("/");
    await page.waitForURL(/\/(onboarding|dashboard)/, { timeout: 60_000 });
    await expect(page.getByTestId("env-badge")).toBeVisible();
    if (/\/onboarding/.test(page.url())) {
      await expect(page.getByRole("heading", { name: /configura tu perfil/i })).toBeVisible();
      await page.getByLabel(/capital inicial/i).fill("10000");
      await page.getByRole("button", { name: /siguiente/i }).click();
      await page.getByRole("button", { name: /siguiente/i }).click();
      await page.getByLabel(/retorno objetivo/i).fill("12");
      await page.getByLabel(/fecha límite/i).fill("2099-01-01");
      await page.getByRole("button", { name: /guardar perfil/i }).click();
      await expect(page.getByRole("heading", { name: /perfil guardado/i })).toBeVisible({ timeout: 30_000 });
      await page.getByRole("link", { name: /ir a mi dashboard/i }).click();
    } else if (!/\/dashboard/.test(page.url())) {
      await page.goto("/dashboard");
    }

    // 3. Dashboard: crear el bot si no hay y arrancarlo.
    await page.waitForURL(/\/dashboard/);
    await expect(page.getByRole("heading", { name: /mi portafolio/i })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("engine-indicator")).toHaveText(/Motor en línea/, { timeout: 60_000 });
    const create = page.getByRole("button", { name: /crear mi bot/i });
    if (await create.isVisible()) await create.click();
    const start = page.getByRole("button", { name: /^arrancar$/i }).first();
    await expect(start).toBeVisible({ timeout: 15_000 });
    if (await start.isEnabled()) await start.click();
    await expect(page.getByText(/Operando/i).first()).toBeVisible({ timeout: 15_000 });

    // 4. El motor opera: aparece al menos una operación nueva en la base (vía la API).
    let trades: Trade[] = [];
    await expect
      .poll(
        async () => {
          trades = await newTrades(request, since);
          return trades.length;
        },
        { timeout: TRADE_TIMEOUT_MS, intervals: [5_000], message: "esperando la primera operación del motor" },
      )
      .toBeGreaterThan(0);
    const trade = trades[0];
    test.info().annotations.push({
      type: "trade",
      description: `${trade.side} ${trade.symbol} (${trade.rationale?.sleeve ?? "?"}, ejecución ${trade.rationale?.execution ?? "testnet"}) a las ${trade.opened_at}`,
    });
    expect(trade.rationale?.sleeve).toBe("demo_pulse");

    // 5. El dashboard la muestra sin recargar (polling de /api/dashboard).
    const list = page.locator("section").filter({ has: page.getByRole("heading", { name: "Últimas operaciones" }) });
    await expect(list.getByText("Demo (pulso)").first()).toBeVisible({ timeout: 60_000 });
    await expect(list.getByText(/BTC\/USDT/).first()).toBeVisible();

    // 6. /api/engine/status dice en línea, con agents y engine.
    const status = await request.get("/api/engine/status");
    expect(status.ok()).toBeTruthy();
    const body = (await status.json()) as { online: boolean; components: { component: string; online: boolean }[] };
    expect(body.online).toBe(true);
    const online = body.components.filter((c) => c.online).map((c) => c.component);
    expect(online).toEqual(expect.arrayContaining(["agents", "engine"]));
  });
});
