import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { checkExport, htmlFileForRoute, routeFromPageDir, scanApp } from "./check-export.mjs";

const webDir = join(dirname(fileURLToPath(import.meta.url)), "..");

function touch(root, path) {
  const full = join(root, path);
  mkdirSync(dirname(full), { recursive: true });
  writeFileSync(full, "");
}

describe("rutas", () => {
  it("convierte carpetas en rutas e ignora grupos", () => {
    expect(routeFromPageDir("")).toBe("/");
    expect(routeFromPageDir("login")).toBe("/login");
    expect(routeFromPageDir(join("(auth)", "onboarding"))).toBe("/onboarding");
  });

  it("mapea rutas a archivos html", () => {
    expect(htmlFileForRoute("/")).toBe("index.html");
    expect(htmlFileForRoute("/a/b")).toBe("a/b.html");
  });

  it("la app real solo tiene las rutas estáticas esperadas", () => {
    const { routes, problems } = scanApp(join(webDir, "src", "app"));
    expect(problems).toEqual([]);
    expect(routes).toEqual(expect.arrayContaining(["/", "/login", "/onboarding"]));
  });
});

describe("checkExport", () => {
  let dir;
  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "check-export-"));
    touch(dir, "src/app/page.tsx");
    touch(dir, "src/app/login/page.tsx");
  });
  afterEach(() => rmSync(dir, { recursive: true, force: true }));

  it("pasa con un out/ completo", () => {
    for (const f of ["out/index.html", "out/login.html", "out/404.html", "out/_next/static/x.js"]) {
      touch(dir, f);
    }
    expect(checkExport({ webDir: dir })).toEqual({ routes: ["/", "/login"], errors: [] });
  });

  it("avisa si no hay out/", () => {
    const { errors } = checkExport({ webDir: dir });
    expect(errors).toHaveLength(1);
    expect(errors[0]).toMatch(/pnpm build/);
  });

  it("reporta rutas faltantes y archivos no soportados", () => {
    touch(dir, "out/index.html");
    touch(dir, "src/app/api/route.ts");
    touch(dir, "src/app/items/[id]/page.tsx");
    touch(dir, "src/middleware.ts");
    const { errors } = checkExport({ webDir: dir });
    expect(errors).toEqual(
      expect.arrayContaining([
        expect.stringMatching(/route handler/),
        expect.stringMatching(/ruta dinámica/),
        "middleware no soportado: middleware.ts",
        "falta login.html para la ruta /login",
        "falta 404.html",
        "falta _next/static",
      ]),
    );
  });
});
