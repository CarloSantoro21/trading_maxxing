#!/usr/bin/env node
/**
 * Verifica que el export estático (out/) contenga todas las rutas de la app.
 *
 * Las rutas esperadas se derivan de los archivos page.* de src/app, así que el chequeo no
 * se desincroniza cuando se agrega una página. También falla si aparece algo que un sitio
 * estático no puede servir (route handlers, middleware o segmentos dinámicos).
 *
 * Uso (después de `pnpm build`):  pnpm check:export
 */
import { existsSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const PAGE_RE = /^page\.(tsx|ts|jsx|js|mdx)$/;
const ROUTE_HANDLER_RE = /^route\.(ts|js)$/;
const MIDDLEWARE_RE = /^middleware\.(ts|js)$/;

function walk(dir) {
  const files = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) files.push(...walk(full));
    else files.push(full);
  }
  return files;
}

/** Convierte la carpeta de un page.* en su ruta pública ("/" para la raíz). */
export function routeFromPageDir(relDir) {
  const segments = relDir
    .split(sep)
    .filter((s) => s && s !== ".")
    // Los grupos "(nombre)" no aparecen en la URL.
    .filter((s) => !(s.startsWith("(") && s.endsWith(")")));
  return "/" + segments.join("/");
}

/** Archivo HTML que Next.js genera para una ruta con trailingSlash desactivado. */
export function htmlFileForRoute(route) {
  return route === "/" ? "index.html" : `${route.slice(1)}.html`;
}

/** Rutas de la app y problemas que impiden el export estático. */
export function scanApp(appDir) {
  const routes = [];
  const problems = [];
  for (const file of walk(appDir)) {
    const name = file.slice(dirname(file).length + 1);
    const relDir = relative(appDir, dirname(file));
    if (ROUTE_HANDLER_RE.test(name)) {
      problems.push(`route handler no soportado en export estático: ${relative(appDir, file)}`);
    }
    if (!PAGE_RE.test(name)) continue;
    const route = routeFromPageDir(relDir);
    if (route.includes("[")) {
      problems.push(`ruta dinámica sin soporte verificado: ${route}`);
      continue;
    }
    routes.push(route);
  }
  return { routes: routes.sort(), problems };
}

/**
 * Revisa out/ contra src/app. Devuelve la lista de errores (vacía si todo está bien).
 * @param {{ webDir: string, outDir?: string }} options
 */
export function checkExport({ webDir, outDir = join(webDir, "out") }) {
  const errors = [];
  const appDir = join(webDir, "src", "app");
  const { routes, problems } = scanApp(appDir);
  errors.push(...problems);

  for (const base of [webDir, join(webDir, "src")]) {
    if (!existsSync(base)) continue;
    for (const entry of readdirSync(base)) {
      if (MIDDLEWARE_RE.test(entry)) errors.push(`middleware no soportado: ${entry}`);
    }
  }

  if (!existsSync(outDir)) {
    errors.push(`no existe ${outDir}; corre \`pnpm build\` primero`);
    return { routes, errors };
  }
  for (const route of routes) {
    const html = htmlFileForRoute(route);
    if (!existsSync(join(outDir, html))) errors.push(`falta ${html} para la ruta ${route}`);
  }
  if (!existsSync(join(outDir, "404.html"))) errors.push("falta 404.html");
  if (!existsSync(join(outDir, "_next", "static"))) errors.push("falta _next/static");
  return { routes, errors };
}

const isMain = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;
if (isMain) {
  const webDir = join(dirname(fileURLToPath(import.meta.url)), "..");
  const { routes, errors } = checkExport({ webDir });
  if (errors.length > 0) {
    for (const e of errors) console.error(`✗ ${e}`);
    process.exit(1);
  }
  console.log(`✓ out/ contiene las ${routes.length} rutas: ${routes.join(", ")}`);
}
