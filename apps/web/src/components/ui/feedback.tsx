"use client";

import { useId, useState } from "react";
import { cardClass, labelClass, inputClass, secondaryButtonClass } from "./styles";

/** Estados de carga, error y vacío compartidos por el dashboard y /strategies. */

export function Loading({ children }: { children: React.ReactNode }) {
  return (
    <div role="status" aria-live="polite" className="flex items-center gap-3 py-10 text-neutral-500">
      <span
        aria-hidden="true"
        className="h-4 w-4 animate-spin rounded-full border-2 border-neutral-300 border-t-emerald-600"
      />
      {children}
    </div>
  );
}

export function ErrorBox({ children, onRetry }: { children: React.ReactNode; onRetry?: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-col gap-3 rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-800 sm:flex-row sm:items-center sm:justify-between dark:border-red-900 dark:bg-red-950 dark:text-red-300"
    >
      <div>{children}</div>
      {onRetry && (
        <button type="button" className={secondaryButtonClass} onClick={onRetry}>
          Reintentar
        </button>
      )}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="rounded-md border border-dashed border-neutral-300 p-6 text-center text-sm text-neutral-600 dark:border-neutral-700 dark:text-neutral-300">
      <p className="font-medium text-neutral-800 dark:text-neutral-100">{title}</p>
      {children && <div className="mt-2">{children}</div>}
    </div>
  );
}

export function Card({
  title,
  action,
  children,
  className = "",
}: {
  title?: React.ReactNode;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={`${cardClass} p-4 sm:p-6 ${className}`}>
      {(title || action) && (
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
          {title && <h2 className="text-lg font-semibold">{title}</h2>}
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

/** Indicador de la suscripción a Supabase Realtime. */
export function LiveBadge({ live }: { live: boolean }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${
        live
          ? "bg-emerald-50 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200"
          : "bg-neutral-100 text-neutral-600 dark:bg-neutral-900 dark:text-neutral-400"
      }`}
    >
      <span aria-hidden="true" className={`h-2 w-2 rounded-full ${live ? "bg-emerald-500" : "bg-neutral-400"}`} />
      {live ? "En vivo" : "Sin conexión en vivo"}
    </span>
  );
}

const confirmTone = {
  danger: "bg-red-700 hover:bg-red-800 focus-visible:outline-red-700",
  primary: "bg-emerald-700 hover:bg-emerald-800 focus-visible:outline-emerald-700",
};

/**
 * Confirmación dentro de la página (en lugar de window.confirm): explica la acción, pide
 * opcionalmente un motivo y solo entonces llama a `onConfirm`.
 */
export function ConfirmPanel({
  title,
  description,
  confirmLabel,
  tone = "primary",
  busy = false,
  error,
  reason,
  onConfirm,
  onCancel,
}: {
  title: string;
  description: React.ReactNode;
  confirmLabel: string;
  tone?: keyof typeof confirmTone;
  busy?: boolean;
  error?: string | null;
  /** Si se da, pide un motivo obligatorio con esta etiqueta. */
  reason?: { label: string; placeholder?: string; initial?: string };
  onConfirm: (reason: string) => void;
  onCancel: () => void;
}) {
  const id = useId();
  const [text, setText] = useState(reason?.initial ?? "");
  const missing = reason !== undefined && text.trim() === "";
  return (
    <div
      role="dialog"
      aria-modal="false"
      aria-labelledby={`${id}-title`}
      aria-describedby={`${id}-desc`}
      className="mt-3 rounded-md border border-neutral-300 bg-neutral-50 p-4 text-sm dark:border-neutral-700 dark:bg-neutral-900"
    >
      <p id={`${id}-title`} className="font-semibold">
        {title}
      </p>
      <div id={`${id}-desc`} className="mt-1 text-neutral-700 dark:text-neutral-300">
        {description}
      </div>
      {reason && (
        <div className="mt-3">
          <label htmlFor={`${id}-reason`} className={labelClass}>
            {reason.label}
          </label>
          <textarea
            id={`${id}-reason`}
            rows={2}
            maxLength={500}
            value={text}
            placeholder={reason.placeholder}
            onChange={(e) => setText(e.target.value)}
            className={`${inputClass} mt-1`}
          />
        </div>
      )}
      {error && (
        <p role="alert" className="mt-2 text-red-700 dark:text-red-400">
          {error}
        </p>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          disabled={busy || missing}
          onClick={() => onConfirm(text.trim())}
          className={`inline-flex items-center justify-center rounded-md px-4 py-2 text-sm font-semibold text-white shadow-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-not-allowed disabled:opacity-60 ${confirmTone[tone]}`}
        >
          {busy ? "Enviando…" : confirmLabel}
        </button>
        <button type="button" disabled={busy} onClick={onCancel} className={secondaryButtonClass}>
          Cancelar
        </button>
      </div>
    </div>
  );
}
