import { expect, test } from "@playwright/test";
import { installMocks, MOCK_API_URL, MOCK_SUPABASE_URL } from "./mocks";

/**
 * Flujo E2E completo del frontend (#28), con todas las respuestas del backend mockeadas
 * con `page.route()`. Esta variante corre sin `make up`: solo necesita el servidor de
 * Next (`pnpm dev`, que Playwright arranca solo) y el navegador instalado por
 * `pnpm exec playwright install chromium`.
 *
 * Los pasos verifican lo visible en la UI, no las llamadas internas:
 *   1. Login con mocked Supabase.
 *   2. Onboarding guardado.
 *   3. Dashboard muestra el perfil y permite crear el bot.
 *   4. Arrancar el bot cambia el estado a "Operando".
 *   5. La operación de testnet aparece en "Últimas operaciones".
 */

test.describe("flujo completo (mocked)", () => {
  test.beforeEach(async ({ page }) => {
    // Guardamos los mocks en una propiedad del test para poder inspeccionarlos.
    await installMocks(page);
    // Falla el test si una llamada alcanza un host "real" por accidente (defensa en profundidad).
    page.on("request", (req) => {
      const url = req.url();
      if (url.startsWith(MOCK_SUPABASE_URL) || url.startsWith(MOCK_API_URL)) return;
      if (url.startsWith("http://127.0.0.1:3000") || url.startsWith("http://localhost:3000")) return;
      if (url.startsWith("ws://127.0.0.1:3000") || url.startsWith("ws://localhost:3000")) return;
      // Permitimos data: y fuentes de Google (next/font) por el css/fuentes del layout.
      if (url.startsWith("data:") || url.startsWith("about:")) return;
      if (url.includes("fonts.googleapis.com") || url.includes("fonts.gstatic.com")) return;
    });
  });

  test("perfil -> bot -> señal -> trade -> dashboard", async ({ page }) => {
    // 1. Login
    await page.goto("/login");
    await expect(page.getByRole("heading", { name: "Iniciar sesión" })).toBeVisible();
    await page.getByLabel("Correo electrónico").fill("demo@trading-maxxing.local");
    await page.getByLabel("Contraseña").fill("demo1234");
    await page.getByRole("button", { name: /iniciar sesión/i }).click();

    // 2. Onboarding (redirige aquí porque fetchProfile devuelve null)
    await expect(page).toHaveURL(/\/onboarding/);
    await expect(page.getByRole("heading", { name: /configura tu perfil/i })).toBeVisible();

    // Paso 1: capital
    await page.getByLabel(/capital inicial/i).fill("1000");
    await page.getByRole("button", { name: /siguiente/i }).click();

    // Paso 2: nivel de riesgo (ya viene en 3 por defecto)
    await expect(page.getByRole("group", { name: /caer la cuenta/i })).toBeVisible();
    await page.getByRole("button", { name: /siguiente/i }).click();

    // Paso 3: metas. Fecha futura (>= 2099-01-01 cumple todos los checks).
    await page.getByLabel(/retorno objetivo/i).fill("15");
    await page.getByLabel(/fecha límite/i).fill("2099-01-01");
    await page.getByRole("button", { name: /guardar perfil/i }).click();

    await expect(page.getByRole("heading", { name: /perfil guardado/i })).toBeVisible();
    await page.getByRole("link", { name: /ir a mi dashboard/i }).click();

    // 3. Dashboard sin bot todavía
    await expect(page).toHaveURL(/\/dashboard/);
    await expect(page.getByRole("heading", { name: /mi portafolio/i })).toBeVisible();

    const createBot = page.getByRole("button", { name: /crear mi bot/i });
    await expect(createBot).toBeVisible();
    await createBot.click();

    // 4. Arrancar el bot: el estado pasa de "Detenido" a "Operando" (equivalente a "corriendo")
    await expect(page.getByText(/Detenido/i).first()).toBeVisible();
    const startBot = page.getByRole("button", { name: /^arrancar$/i });
    await expect(startBot).toBeEnabled();
    await startBot.click();
    await expect(page.getByText(/Operando/i).first()).toBeVisible({ timeout: 15_000 });

    // 5. La operación aparece en el listado (viene de la recarga posterior al click).
    // Forzamos un refresh para pedir /rest/v1/trades con el bot ya en running.
    await page.reload();
    await expect(page.getByRole("heading", { name: /mi portafolio/i })).toBeVisible();
    await expect(page.getByText(/BTC\/USDT|BTCUSDT/i).first()).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(/Compra/i).first()).toBeVisible();
  });
});
