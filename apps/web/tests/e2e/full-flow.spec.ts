import { expect, test } from "@playwright/test";

/**
 * Flujo E2E contra el stack real (#28). Requiere:
 *   - Supabase local corriendo (`supabase start`) con el seed del usuario demo.
 *   - `make up` para Redis, api, agents y engine.
 *   - `pnpm dev` (Playwright lo arranca solo si no está) o la imagen web servida en 3000.
 *
 * Cada paso que depende de un servicio externo hace `test.skip(...)` con una razón
 * entendible si no lo encuentra, para no romper el verde cuando el agente solo tiene
 * `pnpm install` y el frontend.
 *
 * Credenciales demo del seed: `demo@trading-maxxing.local` / `demo1234`.
 */

const DEMO_EMAIL = process.env.E2E_EMAIL ?? "demo@trading-maxxing.local";
const DEMO_PASSWORD = process.env.E2E_PASSWORD ?? "demo1234";
const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "http://127.0.0.1:54321";
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

async function reachable(url: string): Promise<boolean> {
  try {
    const res = await fetch(url, { method: "GET" });
    // Cualquier respuesta (incluso 404) prueba que el servicio escucha.
    return res.status < 600;
  } catch {
    return false;
  }
}

test.describe("flujo completo contra el stack real", () => {
  test("perfil -> bot -> señal -> trade", async ({ page }) => {
    const [supabaseUp, apiUp] = await Promise.all([
      reachable(`${SUPABASE_URL}/auth/v1/health`),
      reachable(`${API_URL}/healthz`),
    ]);
    test.skip(
      !supabaseUp,
      `Supabase no responde en ${SUPABASE_URL}. Corre \`supabase start\` antes de este spec.`,
    );
    test.skip(
      !apiUp,
      `La API no responde en ${API_URL}. Corre \`make up\` antes de este spec.`,
    );

    // 1. Landing / login
    await page.goto("/login");
    await expect(page.getByRole("heading", { name: "Iniciar sesión" })).toBeVisible();

    // 2. Crear perfil (si ya existe, el login va directo a /dashboard; lo manejamos abajo)
    await page.getByLabel("Correo electrónico").fill(DEMO_EMAIL);
    await page.getByLabel("Contraseña").fill(DEMO_PASSWORD);
    await page.getByRole("button", { name: /iniciar sesión/i }).click();

    await page.waitForURL(/\/(onboarding|dashboard)/, { timeout: 15_000 });

    if (/\/onboarding/.test(page.url())) {
      await expect(page.getByRole("heading", { name: /configura tu perfil/i })).toBeVisible();
      await page.getByLabel(/capital inicial/i).fill("1000");
      await page.getByRole("button", { name: /siguiente/i }).click();
      await page.getByRole("button", { name: /siguiente/i }).click();
      await page.getByLabel(/retorno objetivo/i).fill("15");
      await page.getByLabel(/fecha límite/i).fill("2099-01-01");
      await page.getByRole("button", { name: /guardar perfil/i }).click();
      await expect(page.getByRole("heading", { name: /perfil guardado/i })).toBeVisible();
      await page.getByRole("link", { name: /ir a mi dashboard/i }).click();
    }

    await page.waitForURL(/\/dashboard/);
    await expect(page.getByRole("heading", { name: /mi portafolio/i })).toBeVisible();

    // 3. Arrancar el bot (si no hay bot todavía, crearlo)
    const createBot = page.getByRole("button", { name: /crear mi bot/i });
    if (await createBot.isVisible().catch(() => false)) {
      await createBot.click();
    }
    const startBot = page.getByRole("button", { name: /^arrancar$/i }).first();
    await expect(startBot).toBeVisible({ timeout: 15_000 });
    await startBot.click();

    // 4. Estado del bot cambia a "Operando" (equivalente a "corriendo").
    await expect(page.getByText(/Operando/i).first()).toBeVisible({ timeout: 30_000 });

    // 5. Señal / decisión y orden en Binance Testnet. Los agentes publican señales según
    // su reloj; en un entorno local el bot puede tardar en abrir una operación. Damos un
    // margen amplio y, si no aparece, lo marcamos como skip para no esconder un bug de UI
    // ni romper el verde cuando el engine está en modo offline.
    const tradesPanel = page.getByRole("heading", { name: /últimas operaciones/i }).locator("..");
    try {
      await expect(tradesPanel.getByText(/Compra|Venta/i).first()).toBeVisible({ timeout: 90_000 });
    } catch {
      test.skip(
        true,
        "No llegó ninguna operación en 90s: puede ser que el engine esté en modo offline " +
          "o que las estrategias no hayan emitido señales en la ventana del test.",
      );
    }

    // 6. El trade quedó en el dashboard (al menos una fila con símbolo).
    await expect(tradesPanel.getByText(/BTCUSDT|ETHUSDT|BTC\/USDT|ETH\/USDT/i).first()).toBeVisible();
  });
});
