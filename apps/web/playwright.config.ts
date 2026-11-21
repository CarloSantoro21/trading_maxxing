import { defineConfig, devices } from "@playwright/test";

/**
 * Configuración de Playwright para las pruebas E2E / UAT del frontend (#28).
 *
 * Hay dos modos:
 *   - *mocked* (`pnpm e2e:mocked`): intercepta Supabase y la API con `page.route()`, corre
 *     sin servicios externos. Es la variante que corre CI en el push/PR.
 *   - *UAT*    (`pnpm e2e:uat`): apunta a `APP_URL` (el deploy de dev) y corre en el pipeline
 *     después del deploy. Si falla, bloquea la promoción a prod.
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
  // definido) NO arrancamos nada: el frontend ya está desplegado. En local sin APP_URL,
  // inyectamos valores "mocked" para que el cliente de Supabase y el de la API apunten a
  // los hosts que la variante `mocked` intercepta con `page.route()`.
  webServer: SKIP_WEBSERVER
    ? undefined
    : {
        command: "pnpm dev",
        url: BASE_URL,
        reuseExistingServer: true,
        stdout: "ignore",
        stderr: "pipe",
        timeout: 180_000,
        env: {
          NEXT_PUBLIC_SUPABASE_URL:
            process.env.E2E_SUPABASE_URL ?? "http://127.0.0.1:54399",
          NEXT_PUBLIC_SUPABASE_ANON_KEY:
            process.env.E2E_SUPABASE_ANON_KEY ?? "test-anon-key",
          NEXT_PUBLIC_API_URL:
            process.env.E2E_API_URL ?? "http://127.0.0.1:8099",
        },
      },
});
