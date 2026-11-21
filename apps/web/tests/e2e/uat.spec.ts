import { expect, test } from "@playwright/test";

/**
 * UAT contra el deploy de dev (#28). Este spec corre en el pipeline **después** de
 * desplegar a dev y bloquea la promoción a prod si falla. Los valores reales viven en
 * secrets de GitHub (`APP_URL`, `API_URL`, `DEMO_EMAIL`, `DEMO_PASSWORD`); localmente se
 * pueden pasar como variables de entorno al correr `pnpm e2e:uat`.
 *
 *   - `APP_URL`           URL pública del frontend ya desplegado (p. ej. https://dev....).
 *   - `API_URL`           URL pública de la API ya desplegada (opcional). Si no está, el
 *                         healthcheck de API se omite — el resto del flujo sigue.
 *   - `DEMO_EMAIL`        Correo del usuario demo que tiene perfil en dev.
 *   - `DEMO_PASSWORD`     Contraseña del usuario demo.
 *
 * Fail hard: si falta `APP_URL`, el test aborta con un mensaje claro cuando corre en CI
 * (ya desplegado); en local, hace `test.skip` para no obligar a tener el deploy arriba.
 *
 * Credenciales: NUNCA se commitean. Se documentan en `docs/e2e-checklist.md` y en
 * `apps/web/README.md`.
 */

const APP_URL = process.env.APP_URL ?? "";
const API_URL = process.env.API_URL ?? "";
const DEMO_EMAIL = process.env.DEMO_EMAIL ?? "";
const DEMO_PASSWORD = process.env.DEMO_PASSWORD ?? "";
const IS_CI = !!process.env.CI;

function trimSlash(url: string): string {
  return url.replace(/\/$/, "");
}

test.describe("UAT contra dev", () => {
  test.beforeAll(() => {
    if (!APP_URL) {
      if (IS_CI) {
        throw new Error(
          "APP_URL no está definido: la UAT corre después del deploy a dev. " +
            "Configura APP_URL (y DEMO_EMAIL/DEMO_PASSWORD) como secrets del workflow.",
        );
      }
      test.skip(
        true,
        "APP_URL no está definido (corrida local sin deploy). Usa `pnpm e2e:uat` con APP_URL=... para apuntar a dev.",
      );
    }
    if (!DEMO_EMAIL || !DEMO_PASSWORD) {
      throw new Error(
        "DEMO_EMAIL y DEMO_PASSWORD son requeridos para la UAT (ver docs/e2e-checklist.md).",
      );
    }
  });

  test("login → onboarding → dashboard → health", async ({ page, request }) => {
    test.setTimeout(120_000);

    // 1. API /health (si tenemos API_URL): comprobamos que el backend responde.
    //    El endpoint vive en services/api: GET /health -> { service: "api", ... }.
    if (API_URL) {
      const res = await request.get(`${trimSlash(API_URL)}/health`);
      expect(res.ok(), `GET ${API_URL}/health devolvió ${res.status()}`).toBeTruthy();
      const body = await res.json();
      expect(body).toHaveProperty("service");
    }

    // 2. Login contra la URL de dev.
    await page.goto(`${trimSlash(APP_URL)}/login`);
    await expect(page.getByRole("heading", { name: "Iniciar sesión" })).toBeVisible();
    await page.getByLabel("Correo electrónico").fill(DEMO_EMAIL);
    await page.getByLabel("Contraseña").fill(DEMO_PASSWORD);
    await page.getByRole("button", { name: /iniciar sesión/i }).click();

    // 3. Después del login el frontend redirige a /onboarding (si no hay perfil) o
    //    directamente a /dashboard. Ambos son estados válidos para la UAT.
    await page.waitForURL(/\/(onboarding|dashboard)/, { timeout: 30_000 });

    if (/\/onboarding/.test(page.url())) {
      await expect(page.getByRole("heading", { name: /configura tu perfil/i })).toBeVisible();
      // Paso 1: capital inicial.
      await page.getByLabel(/capital inicial/i).fill("1000");
      await page.getByRole("button", { name: /siguiente/i }).click();
      // Paso 2: nivel de riesgo (valor por defecto).
      await page.getByRole("button", { name: /siguiente/i }).click();
      // Paso 3: metas.
      await page.getByLabel(/retorno objetivo/i).fill("15");
      await page.getByLabel(/fecha límite/i).fill("2099-01-01");
      await page.getByRole("button", { name: /guardar perfil/i }).click();
      await expect(page.getByRole("heading", { name: /perfil guardado/i })).toBeVisible({
        timeout: 30_000,
      });
      await page.getByRole("link", { name: /ir a mi dashboard/i }).click();
    }

    // 4. Dashboard: debe cargar el portafolio (encabezado "Mi portafolio").
    await page.waitForURL(/\/dashboard/);
    await expect(page.getByRole("heading", { name: /mi portafolio/i })).toBeVisible({
      timeout: 30_000,
    });

    // 5. Dashboard: el bloque de Bot está presente (sin bot: botón "Crear mi bot";
    //    con bot: tarjeta de controles). Cualquiera de los dos aprueba la UAT.
    await expect(page.getByRole("heading", { name: /^bot$/i })).toBeVisible();

    // 6. Estrategias: la ruta /strategies responde y lista el catálogo compartido.
    await page.goto(`${trimSlash(APP_URL)}/strategies`);
    await expect(page.getByRole("heading", { name: /estrategias/i })).toBeVisible({
      timeout: 30_000,
    });
  });
});
