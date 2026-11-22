import { render, type RenderOptions } from "@testing-library/react";
import type { ReactElement } from "react";
import { ApiProvider } from "@/components/providers/ApiProvider";

/**
 * Renderiza con SWR aislado: caché nueva en cada prueba, sin deduplicar y sin reintentos,
 * para que una prueba no vea los datos de otra.
 */
export function renderWithApi(ui: ReactElement, options?: RenderOptions) {
  return render(
    <ApiProvider value={{ provider: () => new Map(), dedupingInterval: 0, shouldRetryOnError: false }}>{ui}</ApiProvider>,
    options,
  );
}
