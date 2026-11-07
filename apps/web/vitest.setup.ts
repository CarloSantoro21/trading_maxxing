import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Sin `globals: true`, RTL no desmonta solo entre pruebas; lo hacemos aquí.
afterEach(() => cleanup());
