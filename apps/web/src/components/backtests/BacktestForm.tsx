"use client";

import { useState } from "react";
import { errorClass, inputClass, labelClass, primaryButtonClass } from "@/components/ui/styles";
import {
  EMPTY_BACKTEST_FORM,
  MODES,
  STRATEGIES,
  SYMBOLS,
  TIMEFRAMES,
  createBacktest,
  validateBacktest,
  type BacktestFieldErrors,
  type BacktestFormState,
  type BacktestJob,
} from "@/lib/backtests/api";
import type { Fetcher } from "@/lib/tmApi";

function toggle(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

/** Formulario para encolar un backtest con POST /backtests. */
export function BacktestForm({
  token,
  onCreated,
  fetcher,
}: {
  token: string;
  onCreated?: (job: BacktestJob) => void;
  fetcher?: Fetcher;
}) {
  const [form, setForm] = useState<BacktestFormState>(EMPTY_BACKTEST_FORM);
  const [errors, setErrors] = useState<BacktestFieldErrors>({});
  const [status, setStatus] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [message, setMessage] = useState<string | null>(null);

  function set<K extends keyof BacktestFormState>(key: K, value: BacktestFormState[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const result = validateBacktest(form);
    if (!result.ok) {
      setErrors(result.errors);
      return;
    }
    setErrors({});
    setStatus("sending");
    setMessage(null);
    try {
      const job = await createBacktest(token, result.body, fetcher);
      setStatus("sent");
      setMessage(`Backtest encolado (${job.id.slice(0, 8)}). Lo procesa el worker del motor.`);
      onCreated?.(job);
    } catch (err) {
      setStatus("error");
      setMessage(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <form onSubmit={handleSubmit} noValidate className="space-y-4" aria-label="Encolar un backtest">
      <fieldset>
        <legend className={labelClass}>Instrumentos</legend>
        <div className="mt-1 flex gap-4">
          {SYMBOLS.map((s) => (
            <label key={s} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={form.symbols.includes(s)}
                onChange={() => set("symbols", toggle(form.symbols, s))}
              />
              {s}
            </label>
          ))}
        </div>
        {errors.symbols && <p className={errorClass}>{errors.symbols}</p>}
      </fieldset>

      <fieldset>
        <legend className={labelClass}>Estrategias (ninguna = todas)</legend>
        <div className="mt-1 flex flex-wrap gap-4">
          {STRATEGIES.map((s) => (
            <label key={s.value} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={form.strategies.includes(s.value)}
                onChange={() => set("strategies", toggle(form.strategies, s.value))}
              />
              {s.label}
            </label>
          ))}
        </div>
      </fieldset>

      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label htmlFor="bt-timeframe" className={labelClass}>
            Temporalidad
          </label>
          <select
            id="bt-timeframe"
            className={inputClass}
            value={form.timeframe}
            onChange={(e) => set("timeframe", e.target.value)}
          >
            {TIMEFRAMES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="bt-mode" className={labelClass}>
            Modo
          </label>
          <select id="bt-mode" className={inputClass} value={form.mode} onChange={(e) => set("mode", e.target.value)}>
            {MODES.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="bt-start" className={labelClass}>
            Inicio
          </label>
          <input
            id="bt-start"
            type="date"
            className={inputClass}
            value={form.start}
            aria-invalid={Boolean(errors.start)}
            onChange={(e) => set("start", e.target.value)}
          />
          {errors.start && <p className={errorClass}>{errors.start}</p>}
        </div>
        <div>
          <label htmlFor="bt-end" className={labelClass}>
            Fin
          </label>
          <input
            id="bt-end"
            type="date"
            className={inputClass}
            value={form.end}
            aria-invalid={Boolean(errors.end)}
            onChange={(e) => set("end", e.target.value)}
          />
          {errors.end && <p className={errorClass}>{errors.end}</p>}
        </div>
        <div>
          <label htmlFor="bt-capital" className={labelClass}>
            Capital inicial (USDT, opcional)
          </label>
          <input
            id="bt-capital"
            type="number"
            min="0"
            step="any"
            className={inputClass}
            value={form.initialCapital}
            aria-invalid={Boolean(errors.initialCapital)}
            onChange={(e) => set("initialCapital", e.target.value)}
          />
          {errors.initialCapital && <p className={errorClass}>{errors.initialCapital}</p>}
        </div>
      </div>

      <button type="submit" className={primaryButtonClass} disabled={status === "sending"}>
        {status === "sending" ? "Encolando…" : "Encolar backtest"}
      </button>
      {message && (
        <p
          role={status === "error" ? "alert" : "status"}
          className={status === "error" ? errorClass : "text-sm text-emerald-700 dark:text-emerald-400"}
        >
          {message}
        </p>
      )}
    </form>
  );
}
