import { defineConfig, devices } from "@playwright/test";

/**
 * Configuración de Playwright para las pruebas E2E del frontend (#28).
 *
 * El flujo completo (mocked + live) vive en `tests/e2e/`. La variante *mocked* intercepta
 * las llamadas a Supabase y a la API con `page.route()` y corre sin servicios externos;
 * sirve para CI. La variante *live* asume que `make up` levantó Supabase, la API, los
 * agentes y el engine; cada paso que requiere backend hace `test.skip(...)` si no responde.
 *
 * Para correr solo chromium:  `pnpm e2e --project=chromium`
 * Para correr solo la *mocked*: `pnpm e2e:mocked`
 *
 * Si Playwright se queja de que faltan binarios, instala solo chromium:
 *   `pnpm exec playwright install chromium`
 */

const PORT = Number(process.env.E2E_PORT ?? 3000);
const HOST = process.env.E2E_HOST ?? "127.0.0.1";
const BASE_URL = process.env.E2E_BASE_URL ?? `http://${HOST}:${PORT}`;

export default defineConfig({
  testDir: "./tests/e2e",
  // El output cae en test-results/ y playwright-report/, ambos en .gitignore.
  outputDir: "./test-results",
  // Dejamos un margen amplio: el flujo *live* polea al backend (Realtime + señales).
  timeout: 90_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: [
    ["list"],
    ["html", { open: "never", outputFolder: "playwright-report" }],
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
  // Arranca `pnpm dev` si no hay nada escuchando ya. Inyectamos variables públicas con
  // valores "mocked" para que el cliente de Supabase y el de la API apunten a los hosts
  // que el test intercepta con `page.route()`; si ya hay valores en `.env.local`, estos
  // los pisan solo para el proceso del servidor que levanta Playwright.
  webServer: process.env.E2E_SKIP_WEBSERVER
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
