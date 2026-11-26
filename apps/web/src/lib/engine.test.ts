import { describe, expect, it } from "vitest";
import type { EngineComponent, EngineStatus } from "@/lib/api/client";
import { engineState } from "./engine";

function beat(component: string, online: boolean, details?: unknown): EngineComponent {
  return { component, status: "ok", version: null, host: null, started_at: null, last_seen_at: "2026-11-21T10:00:00Z", age_seconds: online ? 5 : 900, online, details };
}

function status(online: boolean, components: EngineComponent[] = []): EngineStatus {
  return { online, last_heartbeat_at: "2026-11-21T10:00:00Z", stale_after_seconds: 120, components };
}

describe("engineState", () => {
  it("sin respuesta todavía está cargando", () => {
    expect(engineState(undefined, undefined)).toEqual({ kind: "loading" });
  });

  it("un error de la API cuenta como motor apagado", () => {
    expect(engineState(status(true), new Error("caída"))).toEqual({ kind: "offline" });
  });

  it("sin latidos recientes está apagado", () => {
    expect(engineState(status(false), undefined)).toEqual({ kind: "offline" });
  });

  it("con agents y engine vivos está listo (y detecta la ejecución simulada)", () => {
    expect(engineState(status(true, [beat("agents", true), beat("engine", true, { mode: "sim" })]), undefined)).toEqual({ kind: "ready", sim: true });
    expect(engineState(status(true, [beat("agents", true), beat("engine", true)]), undefined)).toEqual({ kind: "ready", sim: false });
  });

  it("si falta agents o engine está incompleto", () => {
    expect(engineState(status(true, [beat("agents", true), beat("engine", false)]), undefined)).toEqual({ kind: "partial", missing: ["engine"] });
    expect(engineState(status(true, [beat("engine", true)]), undefined)).toEqual({ kind: "partial", missing: ["agents"] });
  });
});
