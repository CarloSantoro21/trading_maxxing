import { defineConfig, devices } from "@playwright/test";

/**
 * Configuración de Playwright para las pruebas E2E / UAT del frontend (#28).
 *
 * Hay dos modos:
 *   - *mocked* (`pnpm e2e:mocked`): intercepta `/api/*` (mismo origen) con `page.route()`,
 *     corre sin la API ni Cloudflare Access.
 *   - *UAT*    (`pnpm e2e:uat`): apunta a `APP_URL` (el deploy de dev en Cloudflare Pages)
 *     con un service token de Access y corre en el pipeline después del deploy. Si falla,
 *     bloquea la promoción a prod.
 *
 * Resolución del `baseURL`:
 *   1. `APP_URL` (UAT contra dev).
 *   2. `E2E_BASE_URL` (override manual).
 *   3. `http://${E2E_HOST}:${E2E_PORT}` (local, Next dev server).
 *
 * Artefactos para CI:
 *   - `playwright-report/`         — HTML report (subido como artifact).
 *   - `playwright-report/junit.xml` — resultados en JUnit, útiles para dashboards de CI.
 *   - `test-results/`              — trazas, screenshots y videos; ambos en `.gitignore`.
 */

const PORT = Number(process.env.E2E_PORT ?? 3000);
const HOST = process.env.E2E_HOST ?? "127.0.0.1";
const BASE_URL =
  process.env.APP_URL ?? process.env.E2E_BASE_URL ?? `http://${HOST}:${PORT}`;

// Cuando corre la UAT contra un deploy ya publicado (APP_URL definido) no levantamos
// `pnpm dev`: el servidor ya está arriba en la nube.
const SKIP_WEBSERVER = !!process.env.APP_URL || !!process.env.E2E_SKIP_WEBSERVER;

export default defineConfig({
  testDir: "./tests/e2e",
  // El output cae en test-results/ y playwright-report/, ambos en .gitignore.
  outputDir: "./test-results",
  // Dejamos un margen amplio: la UAT contra dev puede tener latencias de red.
  timeout: 90_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: [
    ["list"],
    ["html", { open: "never", outputFolder: "playwright-report" }],
    ["junit", { outputFile: "playwright-report/junit.xml" }],
  ],
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  // Arranca `pnpm dev` si no hay nada escuchando ya. Para la UAT contra dev (`APP_URL`
  // definido) NO arrancamos nada: el frontend ya está desplegado. En la variante mocked
  // `page.route()` responde `/api/*` antes de que llegue al proxy de `next dev`, así que
  // `API_DEV_URL` apunta a un puerto sin nada (si un mock falta, la llamada falla en vez de
  // pegarle a un `wrangler dev` local).
  webServer: SKIP_WEBSERVER
    ? undefined
    : {
        command: `pnpm dev --port ${PORT} --hostname ${HOST}`,
        url: BASE_URL,
        reuseExistingServer: true,
        stdout: "ignore",
        stderr: "pipe",
        timeout: 180_000,
        env: {
          API_DEV_URL: process.env.E2E_API_URL ?? "http://127.0.0.1:8099",
          NEXT_PUBLIC_ENV: "local",
          NEXT_PUBLIC_COMMIT_SHA: process.env.E2E_COMMIT_SHA ?? "e2e0000",
        },
      },
});
