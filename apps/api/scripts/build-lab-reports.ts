/**
 * Genera src/generated/lab-reports.json a partir de docs/estrategias/reportes/*.json.
 *
 * El Worker no tiene sistema de archivos, así que los reportes del laboratorio se
 * empaquetan en build: por cada reporte se guarda el detalle que sirve la API
 * (GET /api/lab/reports/{name}, con el histograma del drawdown ya calculado) y los campos
 * que usa la proyección de metas. Corre solo antes de `wrangler dev`/`deploy` (sección
 * [build] de wrangler.toml), de `pnpm test` y de `pnpm typecheck`.
 *
 * Uso: pnpm build:reports   (LAB_REPORTS_DIR=<dir> para usar otro directorio)
 */

import { mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { type LabBundle, REPORT_NAME, detail, projectionFields } from "../src/lib/lab-core";

const here = dirname(fileURLToPath(import.meta.url));
const sourceDir = resolve(process.env.LAB_REPORTS_DIR ?? join(here, "../../../docs/estrategias/reportes"));
const outFile = resolve(here, "../src/generated/lab-reports.json");

const bundle: LabBundle = { generated_at: new Date().toISOString(), reports: {} };
let files: string[] = [];
try {
  files = readdirSync(sourceDir).filter((f) => f.endsWith(".json")).sort();
} catch {
  console.warn(`[lab-reports] no existe ${sourceDir}; se genera un paquete vacío`);
}

for (const file of files) {
  const name = file.slice(0, -".json".length);
  if (!REPORT_NAME.test(name)) continue;
  try {
    const report = JSON.parse(readFileSync(join(sourceDir, file), "utf8")) as Record<string, unknown>;
    bundle.reports[name] = { detail: detail(name, report), projection: projectionFields(report) };
  } catch (err) {
    // Un archivo ilegible no tumba el build (igual que la lista de la API en Python).
    console.warn(`[lab-reports] se omite ${file}: ${String(err)}`);
  }
}

mkdirSync(dirname(outFile), { recursive: true });
writeFileSync(outFile, JSON.stringify(bundle));
console.log(`[lab-reports] ${Object.keys(bundle.reports).length} reportes -> ${outFile}`);
