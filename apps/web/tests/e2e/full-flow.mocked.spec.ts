import { expect, test } from "@playwright/test";
import { installMocks, MOCK_EMAIL, type MockState } from "./mocks";

/**
 * Flujo E2E completo del frontend (#28, #106), con la API mockeada con `page.route()`.
 * Esta variante solo necesita el servidor de Next (`pnpm dev`, que Playwright arranca
 * solo) y el navegador (`pnpm exec playwright install chromium`): ni `wrangler dev` ni
 * Cloudflare Access. En la nube Access autentica antes de cargar el sitio; aquí
 * `GET /api/me` responde directo con el usuario demo, igual que la API con AUTH_MODE=dev.
 *
 * Los pasos verifican lo visible en la UI:
 *   1. La página de inicio pide /api/me y, sin perfil, manda al onboarding.
 *   2. El encabezado muestra el motor en línea, el badge de ambiente y el logout de Access.
 *   3. Onboarding guardado con PUT /api/onboarding.
 *   4. El dashboard (GET /api/dashboard) permite crear el bot y arrancarlo.
 *   5. La operación de testnet aparece sola en "Últimas operaciones" (polling).
 */

test.describe("flujo completo (mocked)", () => {
  let state: MockState;

  // `next dev` compila cada página la primera vez que se pide y, al terminar, recarga los
  // módulos (Fast Refresh); si eso pasa a media navegación, el `router.replace` se pierde.
  // Pedimos las páginas antes para que ya estén compiladas (en Pages son HTML estático).
  test.beforeAll(async ({ request }) => {
    test.setTimeout(180_000);
    for (const path of ["/", "/onboarding", "/dashboard"]) {
      await request.get(path, { timeout: 120_000 });
    }
  });

  test.beforeEach(async ({ page }) => {
    state = await installMocks(page);
  });

  test("perfil -> bot -> señal -> trade -> dashboard", async ({ page }) => {
    // 1. Inicio: sin perfil, redirige al onboarding.
    await page.goto("/");
    await expect(page).toHaveURL(/\/onboarding/, { timeout: 30_000 });
    await expect(page.getByRole("heading", { name: /configura tu perfil/i })).toBeVisible();

    // 2. Encabezado: usuario, motor, ambiente y cierre de sesión con Access.
    await expect(page.getByText(MOCK_EMAIL)).toBeVisible();
    await expect(page.getByTestId("engine-indicator")).toHaveText(/Motor en línea/);
    await expect(page.getByTestId("env-badge")).toBeVisible();
    await expect(page.getByRole("link", { name: "Cerrar sesión" })).toHaveAttribute("href", "/cdn-cgi/access/logout");

    // 3. Onboarding. Paso 1: capital.
    await page.getByLabel(/capital inicial/i).fill("1000");
    await page.getByRole("button", { name: /siguiente/i }).click();
    // Paso 2: nivel de riesgo (ya viene en 3 por defecto).
    await expect(page.getByRole("group", { name: /caer la cuenta/i })).toBeVisible();
    await page.getByRole("button", { name: /siguiente/i }).click();
    // Paso 3: metas. Fecha futura (>= 2099-01-01 cumple todos los checks).
    await page.getByLabel(/retorno objetivo/i).fill("15");
    await page.getByLabel(/fecha límite/i).fill("2099-01-01");
    await page.getByRole("button", { name: /guardar perfil/i }).click();

    await expect(page.getByRole("heading", { name: /perfil guardado/i })).toBeVisible();
    expect(state.calls).toContain("PUT /onboarding");
    await page.getByRole("link", { name: /ir a mi dashboard/i }).click();

    // 4. Dashboard sin bot todavía: crearlo y arrancarlo.
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 30_000 });
    await expect(page.getByRole("heading", { name: /mi portafolio/i })).toBeVisible();
    const createBot = page.getByRole("button", { name: /crear mi bot/i });
    await expect(createBot).toBeVisible();
    await createBot.click();

    await expect(page.getByText(/Detenido/i).first()).toBeVisible();
    const startBot = page.getByRole("button", { name: /^arrancar$/i });
    await expect(startBot).toBeEnabled();
    await startBot.click();
    await expect(page.getByText(/Operando/i).first()).toBeVisible({ timeout: 15_000 });

    // 5. Sin recargar: el polling de /api/dashboard trae la operación nueva.
    await expect(page.getByText(/BTC\/USDT|BTCUSDT/i).first()).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(/Compra/i).first()).toBeVisible();
  });

  test("si la API dice que el correo no está dado de alta muestra la pantalla de acceso", async ({ page }) => {
    await page.route(
      (url) => url.pathname === "/api/me",
      (route) =>
        route.fulfill({
          status: 403,
          contentType: "application/json",
          body: JSON.stringify({ detail: "El correo no está dado de alta" }),
        }),
    );
    await page.goto("/dashboard");
    await expect(page.getByRole("heading", { name: "No tienes acceso" })).toBeVisible();
    await expect(page.getByText(/Pide al admin que te dé de alta/)).toBeVisible();
  });
});
