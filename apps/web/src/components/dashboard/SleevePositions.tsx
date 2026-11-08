"use client";

import { EmptyState } from "@/components/ui/feedback";
import { NO_SLEEVE, type SleeveSummary } from "@/lib/dashboard/data";
import { formatMoney, formatQty, formatSignedMoney, formatSymbol, strategyLabel } from "@/lib/format";

export function sleeveName(sleeve: string): string {
  return sleeve === NO_SLEEVE ? "Sin estrategia" : strategyLabel(sleeve);
}

function pnlClass(v: number) {
  return v > 0 ? "text-emerald-700 dark:text-emerald-400" : v < 0 ? "text-red-700 dark:text-red-400" : "";
}

/** Posiciones abiertas y P&L realizado de cada sleeve (una estrategia del portafolio). */
export function SleevePositions({ sleeves }: { sleeves: SleeveSummary[] }) {
  if (sleeves.length === 0) {
    return (
      <EmptyState title="Sin posiciones todavía">
        Cuando el bot opere, aquí verás lo que tiene cada estrategia.
      </EmptyState>
    );
  }
  return (
    <ul className="space-y-4">
      {sleeves.map((s) => (
        <li key={s.sleeve} className="rounded-md border border-neutral-200 p-3 dark:border-neutral-800">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h3 className="font-medium">{sleeveName(s.sleeve)}</h3>
            <p className="text-sm text-neutral-600 dark:text-neutral-300">
              Invertido {formatMoney(s.invested)} USDT · P&L realizado{" "}
              <span className={`font-semibold ${pnlClass(s.realizedPnl)}`}>{formatSignedMoney(s.realizedPnl)} USDT</span>{" "}
              ({s.closedTrades} {s.closedTrades === 1 ? "operación cerrada" : "operaciones cerradas"})
            </p>
          </div>
          {s.positions.length === 0 ? (
            <p className="mt-2 text-sm text-neutral-500">Sin posiciones abiertas; todo en USDT.</p>
          ) : (
            <div className="mt-2 overflow-x-auto">
              <table className="w-full min-w-[480px] text-left text-sm">
                <caption className="sr-only">Posiciones abiertas de {sleeveName(s.sleeve)}</caption>
                <thead className="text-xs text-neutral-500">
                  <tr>
                    <th scope="col" className="py-1 pr-3 font-medium">Par</th>
                    <th scope="col" className="py-1 pr-3 text-right font-medium">Cantidad</th>
                    <th scope="col" className="py-1 pr-3 text-right font-medium">Precio de entrada</th>
                    <th scope="col" className="py-1 pr-3 text-right font-medium">Costo (USDT)</th>
                    <th scope="col" className="py-1 text-right font-medium">Stop / objetivo</th>
                  </tr>
                </thead>
                <tbody>
                  {s.positions.map((p) => (
                    <tr key={p.id} className="border-t border-neutral-100 dark:border-neutral-900">
                      <td className="py-1.5 pr-3">{formatSymbol(p.symbol)}</td>
                      <td className="py-1.5 pr-3 text-right tabular-nums">{formatQty(p.quantity)}</td>
                      <td className="py-1.5 pr-3 text-right tabular-nums">{formatMoney(p.entryPrice)}</td>
                      <td className="py-1.5 pr-3 text-right tabular-nums">{formatMoney(p.cost)}</td>
                      <td className="py-1.5 text-right tabular-nums">
                        {formatMoney(p.stopLoss)} / {formatMoney(p.takeProfit)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}
