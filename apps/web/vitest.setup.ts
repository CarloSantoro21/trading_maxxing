import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

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
