import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// Sin `globals: true`, RTL no desmonta solo entre pruebas; lo hacemos aquí.
afterEach(() => cleanup());

// jsdom no trae ResizeObserver; ResponsiveContainer de Recharts lo necesita para medir.
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}

// jsdom no mide el layout, así que ResponsiveContainer vería 0×0 y no dibujaría nada.
// En las pruebas lo reemplazamos por un contenedor de tamaño fijo.
vi.mock("recharts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("recharts")>();
  const { cloneElement, isValidElement } = await import("react");
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) =>
      isValidElement(children)
        ? cloneElement(children as React.ReactElement<{ width?: number; height?: number }>, { width: 800, height: 300 })
        : children,
  };
});
