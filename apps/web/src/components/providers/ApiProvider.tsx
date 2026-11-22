"use client";

import { SWRConfig, type SWRConfiguration } from "swr";
import { ApiError, swrFetcher } from "@/lib/api/client";

/** Cada cuánto se refrescan el dashboard y las estrategias (ms). Polling en lugar de Realtime. */
export const POLL_MS = 5_000;
/** Cada cuánto se consultan el latido del motor y la lista de backtests (ms). */
export const SLOW_POLL_MS = 15_000;

/**
 * Configuración común de SWR: la llave de cada consulta es la ruta de la API (sin `/api`).
 * SWR no refresca con la pestaña oculta (`refreshWhenHidden: false`) ni sin red, y vuelve a
 * pedir los datos al regresar a la pestaña. Un 401/403 no se reintenta.
 */
export const swrDefaults: SWRConfiguration = {
  fetcher: swrFetcher,
  refreshWhenHidden: false,
  refreshWhenOffline: false,
  shouldRetryOnError: (err: unknown) => !(err instanceof ApiError && (err.status === 401 || err.status === 403)),
  errorRetryCount: 3,
};

export function ApiProvider({ children, value }: { children: React.ReactNode; value?: SWRConfiguration }) {
  return <SWRConfig value={{ ...swrDefaults, ...value }}>{children}</SWRConfig>;
}
