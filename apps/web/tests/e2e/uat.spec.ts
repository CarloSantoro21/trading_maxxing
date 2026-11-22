import { expect, test } from "@playwright/test";

/**
 * UAT contra el deploy de dev en Cloudflare Pages (#28). Corre en el pipeline **después**
 * de desplegar a dev y bloquea la promoción a prod si falla.
 *
 *   - `APP_URL`                 URL del sitio de Pages (p. ej. https://tm-web-dev.pages.dev).
 *   - `CF_ACCESS_CLIENT_ID`     Service token de Cloudflare Access con permiso en la app del
 *   - `CF_ACCESS_CLIENT_SECRET` sitio (los manda Playwright como headers en cada petición).
 *   - `EXPECTED_ENV`            Opcional: ambiente que debe mostrar el badge (p. ej. `dev`).
 *
 * Qué verifica:
 *   1. Que `/api/health` responde a través de la Pages Function y el service binding.
 *   2. Que el sitio estático carga y el encabezado muestra el badge de ambiente y versión.
 *   3. Si la identidad del token está dada de alta (GET /api/me 200), recorre el
 *      onboarding o el dashboard y las estrategias. Un service token de Access no trae
 *      correo, así que normalmente la API responde 403 y la app debe mostrar la pantalla
 *      de "No tienes acceso": eso también es un resultado válido (prueba el flujo de auth).
 *
 * Fail hard: sin `APP_URL` la prueba aborta en CI y se omite en local. Las credenciales
 * NUNCA se commitean; viven en secrets de GitHub.
 */

const APP_URL = (process.env.APP_URL ?? "").replace(/\/+$/, "");
const CLIENT_ID = process.env.CF_ACCESS_CLIENT_ID ?? "";
const CLIENT_SECRET = process.env.CF_ACCESS_CLIENT_SECRET ?? "";
const EXPECTED_ENV = process.env.EXPECTED_ENV ?? "";
const IS_CI = !!process.env.CI;

test.use({
  extraHTTPHeaders:
    CLIENT_ID && CLIENT_SECRET ? { "CF-Access-Client-Id": CLIENT_ID, "CF-Access-Client-Secret": CLIENT_SECRET } : {},
});

test.describe("UAT contra dev", () => {
  test.beforeAll(() => {
    if (!APP_URL) {
      if (IS_CI) {
        throw new Error(
          "APP_URL no está definido: la UAT corre después del deploy a dev. " +
            "Configura APP_URL, CF_ACCESS_CLIENT_ID y CF_ACCESS_CLIENT_SECRET como secrets del workflow.",
        );
      }
      test.skip(true, "APP_URL no está definido (corrida local sin deploy). Usa `APP_URL=... pnpm e2e:uat`.");
    }
  });

  test("health → sitio → sesión → dashboard", async ({ page, request }) => {
    test.setTimeout(120_000);

    // 1. La API responde por el mismo origen (Pages Function → service binding → Worker).
    const health = await request.get(`${APP_URL}/api/health`);
    expect(health.ok(), `GET ${APP_URL}/api/health devolvió ${health.status()}`).toBeTruthy();
    expect(await health.json()).toMatchObject({ service: "api", status: "ok" });

    // 2. Inicio: o entra a la app, o muestra la pantalla de "No tienes acceso".
    await page.goto(`${APP_URL}/`);
    const denied = page.getByRole("heading", { name: "No tienes acceso" });
    await Promise.race([
      page.waitForURL(/\/(onboarding|dashboard)/, { timeout: 30_000 }),
      denied.waitFor({ timeout: 30_000 }),
    ]);
    if (await denied.isVisible()) {
      await expect(page.getByRole("link", { name: "Entrar con otro correo" })).toHaveAttribute(
        "href",
        "/cdn-cgi/access/logout",
      );
      test.info().annotations.push({
        type: "auth",
        description: "La identidad del service token no está dada de alta: se validó la pantalla de acceso.",
      });
      return;
    }

    // 3. Encabezado: ambiente y versión del deploy.
    const badge = page.getByTestId("env-badge");
    await expect(badge).toBeVisible();
    if (EXPECTED_ENV) await expect(badge).toContainText(EXPECTED_ENV);
    await expect(page.getByTestId("engine-indicator")).toBeVisible();

    if (/\/onboarding/.test(page.url())) {
      await expect(page.getByRole("heading", { name: /configura tu perfil/i })).toBeVisible();
      await page.getByLabel(/capital inicial/i).fill("1000");
      await page.getByRole("button", { name: /siguiente/i }).click();
      await page.getByRole("button", { name: /siguiente/i }).click();
      await page.getByLabel(/retorno objetivo/i).fill("15");
      await page.getByLabel(/fecha límite/i).fill("2099-01-01");
      await page.getByRole("button", { name: /guardar perfil/i }).click();
      await expect(page.getByRole("heading", { name: /perfil guardado/i })).toBeVisible({ timeout: 30_000 });
      await page.getByRole("link", { name: /ir a mi dashboard/i }).click();
    }

    // 4. Dashboard y estrategias.
    await page.waitForURL(/\/dashboard/);
    await expect(page.getByRole("heading", { name: /mi portafolio/i })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("heading", { name: /^bot$/i })).toBeVisible();

    await page.goto(`${APP_URL}/strategies`);
    await expect(page.getByRole("heading", { name: /estrategias/i, level: 1 })).toBeVisible({ timeout: 30_000 });
  });
});
