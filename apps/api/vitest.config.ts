/**
 * Pruebas dentro del runtime de Workers (workerd vía Miniflare) con una D1 local.
 *
 * @cloudflare/vitest-plugin es el nombre nuevo de @cloudflare/vitest-pool-workers (el
 * paquete viejo quedó deprecado). Las migraciones de migrations/ se leen aquí y se aplican
 * en test/setup.ts antes de cada archivo de pruebas.
 */

import { cloudflareTest, readD1Migrations } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";

export default defineConfig(async () => {
  const migrations = await readD1Migrations("./migrations");
  return {
    plugins: [
      cloudflareTest({
        wrangler: { configPath: "./wrangler.toml", environment: "dev" },
        miniflare: {
          bindings: {
            TEST_MIGRATIONS: migrations,
            SERVICE_TOKEN: "test-service-token",
          },
        },
      }),
    ],
    test: {
      setupFiles: ["./test/setup.ts"],
      // Los logs JSON de cada petición ensucian la salida; se verifican aparte.
      onConsoleLog: () => false,
      reporters: ["default", "junit"],
      outputFile: { junit: "./reports/junit.xml" },
      coverage: {
        provider: "istanbul" as const,
        reporter: ["text", "html", "lcov", "json-summary", "cobertura"],
        reportsDirectory: "./coverage",
        include: ["src/**/*.ts"],
        exclude: ["src/generated/**", "src/**/*.d.ts"],
        thresholds: { lines: 70, statements: 70, functions: 70, branches: 60 },
      },
    },
  };
});
