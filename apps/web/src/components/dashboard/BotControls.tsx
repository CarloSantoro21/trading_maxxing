"use client";

import { useState } from "react";
import { ConfirmPanel } from "@/components/ui/feedback";
import { primaryButtonClass, secondaryButtonClass } from "@/components/ui/styles";
import { apiErrorMessage, killBot, startBot, stopBot, type ApiBot } from "@/lib/api/client";
import type { BotRow } from "@/lib/dashboard/data";
import { botStatusLabel, formatDateTime } from "@/lib/format";

const STATUS_STYLE: Record<string, string> = {
  running: "bg-emerald-50 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200",
  stopped: "bg-neutral-100 text-neutral-700 dark:bg-neutral-900 dark:text-neutral-300",
  paused: "bg-amber-50 text-amber-800 dark:bg-amber-950 dark:text-amber-200",
  killed: "bg-red-50 text-red-800 dark:bg-red-950 dark:text-red-200",
};

const CAN_START = new Set(["stopped", "paused", "killed"]);
const CAN_STOP = new Set(["running", "paused"]);

export function BotStatusBadge({ status }: { status: string }) {
  return (
    <span className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-semibold ${STATUS_STYLE[status] ?? STATUS_STYLE.stopped}`}>
      {botStatusLabel(status)}
    </span>
  );
}

/**
 * Estado del bot y sus botones (API #21): arrancar, detener y paro de emergencia. El paro
 * de emergencia vende todas las posiciones a USDT, así que pide confirmación en la página.
 * La respuesta de la API actualiza el estado al momento; Realtime lo confirma después.
 */
export function BotControls({
  bot,
  token,
  onChange,
}: {
  bot: BotRow;
  token: string;
  onChange: (bot: ApiBot) => void;
}) {
  const [busy, setBusy] = useState<"start" | "stop" | "kill" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmKill, setConfirmKill] = useState(false);

  async function run(action: "start" | "stop" | "kill", reason = "") {
    setBusy(action);
    setError(null);
    try {
      const result =
        action === "start"
          ? await startBot(bot.id, { token })
          : action === "stop"
            ? await stopBot(bot.id, { token })
            : await killBot(bot.id, reason || "paro de emergencia manual", { token });
      onChange(result.bot);
      setConfirmKill(false);
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
        <BotStatusBadge status={bot.status} />
        {since && (
          <span className="text-xs text-neutral-500">
            desde {formatDateTime(since)}
          </span>
        )}
      </div>
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
          disabled={busy !== null || !CAN_START.has(bot.status)}
          onClick={() => run("start")}
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
            setConfirmKill(true);
          }}
        >
          Paro de emergencia
        </button>
      </div>
      {error && !confirmKill && (
        <p role="alert" className="mt-2 text-sm text-red-700 dark:text-red-400">
          {error}
        </p>
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
