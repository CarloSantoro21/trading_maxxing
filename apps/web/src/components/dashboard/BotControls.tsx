"use client";

import { useState } from "react";
import { ConfirmPanel } from "@/components/ui/feedback";
import { primaryButtonClass, secondaryButtonClass } from "@/components/ui/styles";
import { apiErrorMessage, killBot, startBot, stopBot, type ApiBot } from "@/lib/api/client";
import type { BotRow } from "@/lib/dashboard/data";
import { useEngineStatus, type EngineState } from "@/lib/engine";
import { botStatusLabel, formatDateTime } from "@/lib/format";

const STATUS_STYLE: Record<string, string> = {
  running: "bg-emerald-50 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200",
  stopped: "bg-neutral-100 text-neutral-700 dark:bg-neutral-900 dark:text-neutral-300",
  paused: "bg-amber-50 text-amber-800 dark:bg-amber-950 dark:text-amber-200",
  killed: "bg-red-50 text-red-800 dark:bg-red-950 dark:text-red-200",
};

const IDLE_STYLE = "bg-amber-50 text-amber-800 dark:bg-amber-950 dark:text-amber-200";

const CAN_START = new Set(["stopped", "paused", "killed"]);
const CAN_STOP = new Set(["running", "paused"]);

/**
 * Estado del bot. `engineDown`: el bot está encendido en la base pero el motor de la Mac no
 * manda latido, así que no opera; en lugar de "Operando" dice "Encendido (sin motor)".
 */
export function BotStatusBadge({ status, engineDown = false }: { status: string; engineDown?: boolean }) {
  const idle = status === "running" && engineDown;
  return (
    <span
      className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-semibold ${idle ? IDLE_STYLE : (STATUS_STYLE[status] ?? STATUS_STYLE.stopped)}`}
    >
      {idle ? "Encendido (sin motor)" : botStatusLabel(status)}
    </span>
  );
}

/** Qué le falta al motor, en palabras. */
function engineProblem(state: EngineState): string {
  if (state.kind === "partial") {
    return `el motor está incompleto (falta ${state.missing.join(" y ")})`;
  }
  return "el motor no está encendido ni conectado";
}

/** Aviso cuando el bot está encendido pero el motor no corre: no va a hacer nada. */
export function EngineDownBanner({ state }: { state: EngineState }) {
  return (
    <div
      role="status"
      data-testid="engine-down-banner"
      className="mt-3 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200"
    >
      <p className="font-semibold">Tu bot está encendido, pero no va a operar: {engineProblem(state)}.</p>
      <p className="mt-1">
        Sin motor no se generan señales ni se mandan órdenes, y nadie vigila el stop loss ni el take
        profit de las posiciones abiertas. En cuanto el motor vuelva a mandar latido, el bot empieza a
        operar solo; no hace falta volver a arrancarlo.
      </p>
    </div>
  );
}

/**
 * Estado del bot y sus botones (API #21): arrancar, detener y paro de emergencia. El paro
 * de emergencia vende todas las posiciones a USDT, así que pide confirmación en la página.
 * La respuesta de la API actualiza el estado al momento; el siguiente refresco lo confirma.
 */
export function BotControls({
  bot,
  onChange,
}: {
  bot: BotRow;
  onChange: (bot: ApiBot) => void;
}) {
  const [busy, setBusy] = useState<"start" | "stop" | "kill" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmKill, setConfirmKill] = useState(false);
  const [confirmStart, setConfirmStart] = useState(false);
  const { state: engine } = useEngineStatus();
  const engineDown = engine.kind === "offline" || engine.kind === "partial";

  async function run(action: "start" | "stop" | "kill", reason = "") {
    setBusy(action);
    setError(null);
    try {
      const result =
        action === "start"
          ? await startBot(bot.id)
          : action === "stop"
            ? await stopBot(bot.id)
            : await killBot(bot.id, reason || "paro de emergencia manual");
      onChange(result.bot);
      setConfirmKill(false);
      setConfirmStart(false);
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  const since = bot.status === "running" ? bot.started_at : bot.stopped_at;
  return (
    <div>
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-medium">{bot.name}</span>
        <BotStatusBadge status={bot.status} engineDown={engineDown} />
        {since && (
          <span className="text-xs text-neutral-500">
            desde {formatDateTime(since)}
          </span>
        )}
      </div>
      {bot.status === "running" && engineDown && <EngineDownBanner state={engine} />}
      {bot.status === "killed" && (
        <p className="mt-2 text-sm text-red-700 dark:text-red-400">
          El bot se detuvo por un paro de emergencia (manual o porque la cuenta tocó tu caída máxima).
          Sus posiciones se vendieron a USDT.
        </p>
      )}
      <div className="mt-4 flex flex-wrap gap-2">
        <button
          type="button"
          className={primaryButtonClass}
          disabled={busy !== null || confirmStart || !CAN_START.has(bot.status)}
          onClick={() => {
            if (engineDown) {
              setError(null);
              setConfirmKill(false);
              setConfirmStart(true);
            } else {
              void run("start");
            }
          }}
        >
          {busy === "start" ? "Arrancando…" : "Arrancar"}
        </button>
        <button
          type="button"
          className={secondaryButtonClass}
          disabled={busy !== null || !CAN_STOP.has(bot.status)}
          onClick={() => run("stop")}
        >
          {busy === "stop" ? "Deteniendo…" : "Detener"}
        </button>
        <button
          type="button"
          className="inline-flex items-center justify-center rounded-md border border-red-300 px-4 py-2 text-sm font-semibold text-red-700 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-60 dark:border-red-800 dark:text-red-400 dark:hover:bg-red-950"
          disabled={busy !== null || confirmKill}
          onClick={() => {
            setError(null);
            setConfirmStart(false);
            setConfirmKill(true);
          }}
        >
          Paro de emergencia
        </button>
      </div>
      {error && !confirmKill && !confirmStart && (
        <p role="alert" className="mt-2 text-sm text-red-700 dark:text-red-400">
          {error}
        </p>
      )}
      {confirmStart && (
        <ConfirmPanel
          title="El motor está apagado: tu bot no va a hacer nada"
          description={
            <>
              Ahora mismo {engineProblem(engine)}. Puedes encender el bot de todos modos: queda
              encendido y empieza a operar solo en cuanto el motor se conecte, pero mientras tanto no
              abre ni cierra operaciones.
            </>
          }
          confirmLabel="Encender de todos modos"
          busy={busy === "start"}
          error={error}
          onConfirm={() => run("start")}
          onCancel={() => {
            setConfirmStart(false);
            setError(null);
          }}
        />
      )}
      {confirmKill && (
        <ConfirmPanel
          title="¿Activar el paro de emergencia?"
          description="El bot vende todas sus posiciones a USDT al precio de mercado y se detiene. Puedes volver a arrancarlo después."
          confirmLabel="Sí, vender todo y detener"
          tone="danger"
          busy={busy === "kill"}
          error={error}
          onConfirm={() => run("kill")}
          onCancel={() => {
            setConfirmKill(false);
            setError(null);
          }}
        />
      )}
      <p className="mt-3 text-xs text-neutral-500">
        Detener deja de abrir operaciones nuevas y conserva las abiertas; el paro de emergencia además las cierra.
      </p>
    </div>
  );
}
