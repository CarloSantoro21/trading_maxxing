import { applyD1Migrations, env } from "cloudflare:test";

// Migraciones idempotentes: applyD1Migrations solo aplica las que falten.
await applyD1Migrations(env.DB, env.TEST_MIGRATIONS);
