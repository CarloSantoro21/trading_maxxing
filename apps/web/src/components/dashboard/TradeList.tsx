"use client";

import { sleeveName } from "@/components/dashboard/SleevePositions";
import { EmptyState } from "@/components/ui/feedback";
import { sleeveOf, type TradeRow } from "@/lib/dashboard/data";
import { describeRationale, type Tone } from "@/lib/dashboard/rationale";
import { formatDateTime, formatMoney, formatQty, formatSignedMoney, formatSymbol, toNumber } from "@/lib/format";

const TONE_DOT: Record<Tone, string> = {
  neutral: "bg-neutral-400",
  good: "bg-emerald-500",
  bad: "bg-red-500",
};

/** Últimas operaciones con su justificación (señales, simulación, veto y riesgo). */
export function TradeList({ trades }: { trades: TradeRow[] }) {
  if (trades.length === 0) {
    return (
      <EmptyState title="Aún no hay operaciones">
        El bot abre una operación cuando las estrategias, la simulación y el control de riesgo coinciden.
      </EmptyState>
    );
  }
  return (
    <ul className="divide-y divide-neutral-100 dark:divide-neutral-900">
      {trades.map((t) => {
        const open = t.closed_at === null;
        const pnl = toNumber(t.pnl);
        const items = describeRationale(t.rationale);
        return (
          <li key={t.id} className="py-3">
            <details className="group">
              <summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-3 gap-y-1 [&::-webkit-details-marker]:hidden">
                <span
                  className={`rounded px-1.5 py-0.5 text-xs font-semibold ${
                    t.side === "buy"
                      ? "bg-emerald-50 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200"
                      : "bg-red-50 text-red-800 dark:bg-red-950 dark:text-red-200"
                  }`}
                >
                  {t.side === "buy" ? "Compra" : "Venta"}
                </span>
                <span className="font-medium">{formatSymbol(t.symbol)}</span>
                <span className="text-sm text-neutral-600 dark:text-neutral-300">
                  {formatQty(toNumber(t.quantity))} @ {formatMoney(toNumber(t.entry_price))}
                </span>
                <span className="text-xs text-neutral-500">{sleeveName(sleeveOf(t))}</span>
                <span className="ml-auto text-sm tabular-nums">
                  {open ? (
                    <span className="text-neutral-500">Abierta</span>
                  ) : (
                    <span
                      className={
                        (pnl ?? 0) > 0
                          ? "text-emerald-700 dark:text-emerald-400"
                          : (pnl ?? 0) < 0
                            ? "text-red-700 dark:text-red-400"
                            : ""
                      }
                    >
                      {formatSignedMoney(pnl)} USDT
                    </span>
                  )}
                </span>
                <span className="w-full text-xs text-neutral-500 sm:w-auto">
                  {formatDateTime(t.opened_at)}
                  {t.closed_at ? ` → ${formatDateTime(t.closed_at)}` : ""}
                  <span className="ml-2 text-emerald-700 group-open:hidden dark:text-emerald-400">¿Por qué?</span>
                </span>
              </summary>
              <div className="mt-2 rounded-md bg-neutral-50 p-3 text-sm dark:bg-neutral-900">
                {items.length === 0 ? (
                  <p className="text-neutral-500">Esta operación no trae justificación guardada.</p>
                ) : (
                  <ul className="space-y-1.5">
                    {items.map((item, i) => (
                      <li key={i} className="flex gap-2">
                        <span aria-hidden="true" className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${TONE_DOT[item.tone]}`} />
                        <span>
                          <strong className="font-medium">{item.label}:</strong> {item.text}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
                {(t.exit_price !== null || (toNumber(t.fees) ?? 0) > 0) && (
                  <p className="mt-2 text-xs text-neutral-500">
                    {t.exit_price !== null && `Salida a ${formatMoney(toNumber(t.exit_price))}. `}
                    Comisiones: {formatMoney(toNumber(t.fees))} USDT.
                  </p>
                )}
              </div>
            </details>
          </li>
        );
      })}
    </ul>
  );
}
