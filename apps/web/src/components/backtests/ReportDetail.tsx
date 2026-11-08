"use client";

import { cardClass } from "@/components/ui/styles";
import type { LabReportDetail, Metrics } from "@/lib/backtests/api";
import { num, pct } from "@/lib/backtests/format";
import { DrawdownHistogramChart, EquityChart } from "./ReportCharts";

export function VerdictBadge({ approved }: { approved: boolean }) {
  return approved ? (
    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2.5 py-0.5 text-sm font-medium text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300">
      <span aria-hidden>✓</span> Aprobada
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded-full bg-red-100 px-2.5 py-0.5 text-sm font-medium text-red-800 dark:bg-red-950 dark:text-red-300">
      <span aria-hidden>✗</span> No aprobada
    </span>
  );
}

const METRIC_ROWS: { key: keyof Metrics; label: string; fmt: (v?: number) => string }[] = [
  { key: "total_return", label: "Retorno total", fmt: (v) => pct(v) },
  { key: "cagr", label: "CAGR", fmt: (v) => pct(v) },
  { key: "sharpe", label: "Sharpe", fmt: (v) => num(v) },
  { key: "volatility", label: "Volatilidad anual", fmt: (v) => pct(v) },
  { key: "max_drawdown", label: "Drawdown máximo", fmt: (v) => pct(v) },
];

export function MetricsTable({ strategy, hold }: { strategy: Metrics; hold: Metrics }) {
  return (
    <table className="w-full text-sm">
      <caption className="sr-only">Métricas out-of-sample contra comprar y mantener</caption>
      <thead>
        <tr className="text-left text-neutral-500">
          <th scope="col" className="py-1 font-medium">Métrica</th>
          <th scope="col" className="py-1 text-right font-medium">Estrategia</th>
          <th scope="col" className="py-1 text-right font-medium">Comprar y mantener</th>
        </tr>
      </thead>
      <tbody className="tabular-nums">
        {METRIC_ROWS.map((m) => (
          <tr key={m.key} className="border-t border-neutral-100 dark:border-neutral-800">
            <th scope="row" className="py-1 text-left font-normal">{m.label}</th>
            <td className="py-1 text-right">{m.fmt(strategy[m.key])}</td>
            <td className="py-1 text-right">{m.fmt(hold[m.key])}</td>
          </tr>
        ))}
        <tr className="border-t border-neutral-100 dark:border-neutral-800">
          <th scope="row" className="py-1 text-left font-normal">Trades</th>
          <td className="py-1 text-right">{strategy.trades ?? "—"}</td>
          <td className="py-1 text-right">—</td>
        </tr>
      </tbody>
    </table>
  );
}

function WindowsTable({ windows }: { windows: LabReportDetail["windows"] }) {
  if (windows.length === 0) return <p className="text-sm text-neutral-500">El reporte no trae ventanas.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-sm">
        <caption className="sr-only">Resultados por ventana del walk-forward</caption>
        <thead>
          <tr className="text-left text-neutral-500">
            <th scope="col" className="py-1 pr-2 font-medium">Ventana</th>
            <th scope="col" className="py-1 pr-2 font-medium">Out-of-sample</th>
            <th scope="col" className="py-1 pr-2 font-medium">Parámetros</th>
            <th scope="col" className="py-1 pr-2 text-right font-medium">Sharpe IS</th>
            <th scope="col" className="py-1 pr-2 text-right font-medium">Retorno OOS</th>
            <th scope="col" className="py-1 pr-2 text-right font-medium">Comprar y mantener</th>
            <th scope="col" className="py-1 pr-2 text-right font-medium">Sharpe OOS</th>
            <th scope="col" className="py-1 pr-2 text-right font-medium">DD máx. OOS</th>
            <th scope="col" className="py-1 text-right font-medium">Trades</th>
          </tr>
        </thead>
        <tbody className="tabular-nums">
          {windows.map((w, i) => (
            <tr key={w.label ?? i} className="border-t border-neutral-100 dark:border-neutral-800">
              <th scope="row" className="py-1 pr-2 text-left font-normal">{w.label ?? i + 1}</th>
              <td className="py-1 pr-2">
                {w.oos_start} → {w.oos_end}
              </td>
              <td className="py-1 pr-2 font-mono text-xs">{w.params_label ?? "—"}</td>
              <td className="py-1 pr-2 text-right">{num(w.is_sharpe)}</td>
              <td
                className={`py-1 pr-2 text-right ${
                  (w.oos_return ?? 0) < 0 ? "text-red-700 dark:text-red-400" : ""
                }`}
              >
                {pct(w.oos_return)}
              </td>
              <td className="py-1 pr-2 text-right">{pct(w.hold_return)}</td>
              <td className="py-1 pr-2 text-right">{num(w.oos_sharpe)}</td>
              <td className="py-1 pr-2 text-right">{pct(w.oos_max_drawdown)}</td>
              <td className="py-1 text-right">{w.trades ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Verdict({ report }: { report: LabReportDetail }) {
  return (
    <section className={cardClass} aria-labelledby="verdict-title">
      <div className="flex flex-wrap items-center gap-3">
        <h2 id="verdict-title" className="text-lg font-semibold">
          Veredicto
        </h2>
        <VerdictBadge approved={report.approved} />
      </div>
      {report.checks.length > 0 ? (
        <ul className="mt-3 space-y-1 text-sm">
          {report.checks.map((c) => (
            <li key={c.name} className="flex gap-2">
              <span aria-hidden className={c.passed ? "text-emerald-700 dark:text-emerald-400" : "text-red-700 dark:text-red-400"}>
                {c.passed ? "✓" : "✗"}
              </span>
              <span>
                <span className="sr-only">{c.passed ? "Cumple: " : "No cumple: "}</span>
                {c.reason ?? c.name}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        report.reasons.length > 0 && (
          <ul className="mt-3 list-disc pl-5 text-sm">
            {report.reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        )
      )}
      {Object.keys(report.exposure_by_risk_level).length > 0 && (
        <p className="mt-3 text-sm text-neutral-600 dark:text-neutral-300">
          Exposición proporcional por nivel de riesgo:{" "}
          {Object.entries(report.exposure_by_risk_level)
            .map(([level, k]) => `nivel ${level}: ${pct(k, 0)}`)
            .join(" · ")}
        </p>
      )}
    </section>
  );
}

export function ReportDetail({ report }: { report: LabReportDetail }) {
  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">
          Reporte del laboratorio: <span className="font-mono">{report.name}</span>
        </h1>
        <p className="mt-1 text-sm text-neutral-600 dark:text-neutral-300">
          {report.symbols.join(", ")} · out-of-sample de {report.oos_start} a {report.oos_end} ·
          comisión de 0.1 %
        </p>
      </header>

      <Verdict report={report} />

      <section className={cardClass} aria-labelledby="equity-title">
        <h2 id="equity-title" className="mb-3 text-lg font-semibold">
          Equity out-of-sample contra comprar y mantener
        </h2>
        <EquityChart equity={report.equity} />
        <div className="mt-4">
          <MetricsTable strategy={report.aggregate} hold={report.buy_and_hold} />
        </div>
      </section>

      <section className={cardClass} aria-labelledby="mc-title">
        <h2 id="mc-title" className="mb-3 text-lg font-semibold">
          Drawdown máximo del Monte Carlo
        </h2>
        {report.drawdown_histogram ? (
          <DrawdownHistogramChart histogram={report.drawdown_histogram} />
        ) : (
          <p className="text-sm text-neutral-500">
            Drawdown p95: {pct(report.max_drawdown_p95)}. El reporte no trae la curva diaria para
            dibujar el histograma.
          </p>
        )}
      </section>

      <section className={cardClass} aria-labelledby="windows-title">
        <h2 id="windows-title" className="mb-3 text-lg font-semibold">
          Ventanas del walk-forward ({report.windows.length})
        </h2>
        <WindowsTable windows={report.windows} />
      </section>
    </div>
  );
}
