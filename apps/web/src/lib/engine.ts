"use client";

import useSWR from "swr";
import { SLOW_POLL_MS } from "@/components/providers/ApiProvider";
import type { EngineStatus } from "@/lib/api/client";

/**
 * Para que un bot opere hacen falta los dos procesos del motor de la Mac: `agents` (lee los
 * comandos start/stop/kill, corre las estrategias y decide) y `engine` (ejecuta las órdenes).
 */
export const REQUIRED_COMPONENTS = ["agents", "engine"] as const;

export type EngineState =
  /** Todavía no llega la primera respuesta. */
  | { kind: "loading" }
  /** agents y engine mandan latido: los bots encendidos operan. */
  | { kind: "ready"; sim: boolean }
  /** Ningún latido reciente (o la API no respondió): los bots no hacen nada. */
  | { kind: "offline" }
  /** Solo una parte del motor está viva: tampoco se opera. */
  | { kind: "partial"; missing: string[] };

export function engineState(data: EngineStatus | undefined, error: unknown): EngineState {
  if (error) return { kind: "offline" };
  if (data === undefined) return { kind: "loading" };
  if (!data.online) return { kind: "offline" };
  const components = data.components ?? [];
  // Respuesta sin detalle por componente: nos quedamos con el `online` global.
  if (components.length === 0) return { kind: "ready", sim: false };
  const alive = (name: string) => components.some((c) => c.component === name && c.online);
  const missing = REQUIRED_COMPONENTS.filter((name) => !alive(name));
  if (missing.length > 0) return { kind: "partial", missing: [...missing] };
  const engine = components.find((c) => c.component === "engine");
  return { kind: "ready", sim: (engine?.details as { mode?: string } | undefined)?.mode === "sim" };
}

/** Estado del motor (GET /api/engine/status). Comparte la caché de SWR con el encabezado. */
export function useEngineStatus() {
  const { data, error } = useSWR<EngineStatus>("/engine/status", { refreshInterval: SLOW_POLL_MS });
  return { data, error, state: engineState(data, error) };
}
