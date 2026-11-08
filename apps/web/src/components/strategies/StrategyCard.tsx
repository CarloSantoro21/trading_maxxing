"use client";

import { useState } from "react";
import { ReferenceEquityChart } from "@/components/strategies/ReferenceEquityChart";
import { ConfirmPanel, EmptyState } from "@/components/ui/feedback";
import { cardClass, primaryButtonClass, secondaryButtonClass } from "@/components/ui/styles";
import { apiErrorMessage, transitionStrategy, type StrategyAction } from "@/lib/api/client";
import {
  actorLabel,
  availableActions,
  bandPosition,
  drawdownBand,
  labSummary,
  latestRun,
  mcBand,
  MIN_SHADOW_DAYS,
  referenceSeries,
  type ReferenceEquityRow,
  type StrategyRow,
  type TransitionRow,
} from "@/lib/strategies/data";
import { formatDateTime, formatMoney, formatPct, stateLabel, strategyLabel, toNumber } from "@/lib/format";

const STATE_STYLE: Record<string, string> = {
  research: "bg-neutral-100 text-neutral-700 dark:bg-neutral-900 dark:text-neutral-300",
  lab: "bg-sky-50 text-sky-800 dark:bg-sky-950 dark:text-sky-200",
  shadow: "bg-violet-50 text-violet-800 dark:bg-violet-950 dark:text-violet-200",
  active: "bg-emerald-50 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200",
  probation: "bg-amber-50 text-amber-800 dark:bg-amber-950 dark:text-amber-200",
  retired: "bg-red-50 text-red-800 dark:bg-red-950 dark:text-red-200",
};

export function StateBadge({ state }: { state: string }) {
  return (
    <span className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-semibold ${STATE_STYLE[state] ?? STATE_STYLE.research}`}>
      {stateLabel(state)}
    </span>
  );
}

const BAND_TEXT = {
  inside: { text: "Dentro de la banda esperada por el laboratorio.", cls: "text-emerald-700 dark:text-emerald-400" },
  below: { text: "Por debajo del p5 esperado: va peor que lo que anticipaba el laboratorio.", cls: "text-red-700 dark:text-red-400" },
  above: { text: "Por encima del p95 esperado: va mejor que lo anticipado (revisa que no sea suerte).", cls: "text-amber-700 dark:text-amber-400" },
};

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-neutral-500">{label}</dt>
      <dd className="font-medium tabular-nums">{value}</dd>
    </div>
  );
}

/** Una estrategia: estado, equity de referencia contra el MC, reporte del lab, historial y acciones. */
export function StrategyCard({
  strategy,
  equity,
  transitions,
  isAdmin,
  token,
  currentUserId,
  onTransition,
}: {
  strategy: StrategyRow;
  equity: ReferenceEquityRow[];
  transitions: TransitionRow[];
  isAdmin: boolean;
  token: string;
  currentUserId?: string;
  onTransition: () => void;
}) {
  const [confirm, setConfirm] = useState<StrategyAction | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const label = strategyLabel(strategy.name);
  const band = mcBand(strategy.lab_report);
  const points = referenceSeries(latestRun(equity, strategy.id), band);
  const position = bandPosition(points);
  const dd = drawdownBand(strategy.lab_report);
  const lab = labSummary(strategy.lab_report);
  const live = strategy.live_metrics;
  const currentDd = toNumber(live.drawdown) ?? (points.length ? points[points.length - 1].drawdown : null);
  const actions = availableActions(strategy);
  const history = transitions.filter((t) => t.strategy_id === strategy.id);
  const failures = toNumber((live.shadow as Record<string, unknown> | undefined)?.failures);

  async function handleConfirm(reason: string) {
    if (!confirm) return;
    setBusy(true);
    setError(null);
    try {
      const result = await transitionStrategy(strategy.name, confirm, reason, { token });
      setNotice(`${label} pasó de ${stateLabel(result.from_state)} a ${stateLabel(result.to_state)}.`);
      setConfirm(null);
      onTransition();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className={`${cardClass} p-4 sm:p-6`} aria-labelledby={`strategy-${strategy.name}`}>
      <header className="flex flex-wrap items-center gap-3">
        <h2 id={`strategy-${strategy.name}`} className="text-lg font-semibold">
          {label}
        </h2>
        <code className="text-xs text-neutral-500">{strategy.name}</code>
        <StateBadge state={strategy.state} />
        <span className="text-xs text-neutral-500">desde {formatDateTime(strategy.state_changed_at)}</span>
      </header>

      {strategy.state === "shadow" && (
        <p className="mt-2 text-sm">
          <strong className="tabular-nums">{(strategy.shadow_days_clean ?? 0).toFixed(1)}</strong> días en shadow sin
          fallas (se necesitan {MIN_SHADOW_DAYS} para promoverla)
          {failures ? ` · ${failures} ${failures === 1 ? "falla" : "fallas"} en esta corrida` : ""}.
        </p>
      )}

      <div className="mt-4 grid gap-6 lg:grid-cols-5">
        <section className="lg:col-span-3" aria-label="Portafolio de referencia">
          <h3 className="text-sm font-semibold">Portafolio de referencia</h3>
          <p className="text-xs text-neutral-500">
            Corre a tamaño completo con las mismas señales que reciben los bots; es con lo que se juzga a la estrategia.
          </p>
          <div className="mt-2">
            {points.length === 0 ? (
              <EmptyState title="Sin curva de referencia">
                Aparece cuando la estrategia entra a shadow y el ledger escribe su primer día.
              </EmptyState>
            ) : (
              <>
                <ReferenceEquityChart points={points} name={label} />
                {position && <p className={`mt-2 text-sm ${BAND_TEXT[position].cls}`}>{BAND_TEXT[position].text}</p>}
                {!band && (
                  <p className="mt-2 text-xs text-neutral-500">
                    El reporte del laboratorio no trae la distribución del retorno, así que no hay banda que dibujar.
                  </p>
                )}
              </>
            )}
          </div>
          <dl className="mt-3 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
            <Metric label="Equity" value={formatMoney(toNumber(live.equity) ?? (points.at(-1)?.equity ?? null), 0)} />
            <Metric label="Retorno" value={formatPct(toNumber(live.total_return), 1, true)} />
            <Metric label="Caída actual" value={formatPct(currentDd)} />
            <Metric
              label="Caída p95 / p99 del MC"
              value={dd ? `${formatPct(dd.p95)} / ${formatPct(dd.p99)}` : "—"}
            />
          </dl>
        </section>

        <section className="lg:col-span-2" aria-label="Reporte del laboratorio">
          <h3 className="text-sm font-semibold">Reporte del laboratorio</h3>
          {!lab ? (
            <p className="mt-2 text-sm text-neutral-500">Todavía no pasa por el laboratorio.</p>
          ) : (
            <>
              {lab.approved !== null && (
                <p className={`mt-2 text-sm font-medium ${lab.approved ? "text-emerald-700 dark:text-emerald-400" : "text-red-700 dark:text-red-400"}`}>
                  {lab.approved ? "Aprobada por los criterios" : "No pasó los criterios"}
                  {lab.start && lab.end ? ` (walk-forward ${lab.start.slice(0, 4)}–${lab.end.slice(0, 4)})` : ""}
                </p>
              )}
              <dl className="mt-2 grid grid-cols-2 gap-3 text-sm">
                <Metric label="Sharpe OOS" value={lab.sharpe === null ? "—" : lab.sharpe.toFixed(2)} />
                <Metric label="CAGR" value={formatPct(lab.cagr)} />
                <Metric label="Caída máxima" value={formatPct(lab.maxDrawdown)} />
                <Metric label="Trades" value={lab.trades === null ? "—" : String(lab.trades)} />
              </dl>
              {lab.checks.length > 0 && (
                <ul className="mt-3 space-y-1 text-sm">
                  {lab.checks.map((c) => (
                    <li key={c.name} className="flex gap-2">
                      <span aria-hidden="true">{c.passed ? "✓" : "✗"}</span>
                      <span className={c.passed ? "" : "text-red-700 dark:text-red-400"}>
                        <span className="sr-only">{c.passed ? "Cumple: " : "No cumple: "}</span>
                        {c.reason}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </section>
      </div>

      <details className="mt-4">
        <summary className="cursor-pointer text-sm font-semibold">Historial de estados ({history.length})</summary>
        {history.length === 0 ? (
          <p className="mt-2 text-sm text-neutral-500">Sin transiciones registradas.</p>
        ) : (
          <ol className="mt-2 space-y-2 text-sm">
            {history.map((t) => (
              <li key={t.id} className="border-l-2 border-neutral-200 pl-3 dark:border-neutral-800">
                <p>
                  <span className="font-medium">
                    {stateLabel(t.from_state)} → {stateLabel(t.to_state)}
                  </span>{" "}
                  <span className="text-neutral-500">
                    · {actorLabel(t.actor, currentUserId)} · {formatDateTime(t.created_at)}
                  </span>
                </p>
                <p className="text-neutral-600 dark:text-neutral-300">{t.reason}</p>
              </li>
            ))}
          </ol>
        )}
      </details>

      {notice && (
        <p role="status" className="mt-4 text-sm text-emerald-700 dark:text-emerald-400">
          {notice}
        </p>
      )}

      {isAdmin && (actions.canPromote || actions.promoteHint || actions.canRetire) && (
        <div className="mt-4 border-t border-neutral-100 pt-4 dark:border-neutral-900">
          <div className="flex flex-wrap items-center gap-2">
            {(actions.canPromote || actions.promoteHint) && (
              <button
                type="button"
                className={primaryButtonClass}
                disabled={!actions.canPromote || busy || confirm !== null}
                onClick={() => {
                  setError(null);
                  setConfirm("promote");
                }}
              >
                Promover a activa
              </button>
            )}
            {actions.canRetire && (
              <button
                type="button"
                className={secondaryButtonClass}
                disabled={busy || confirm !== null}
                onClick={() => {
                  setError(null);
                  setConfirm("retire");
                }}
              >
                Retirar
              </button>
            )}
            {actions.promoteHint && <span className="text-xs text-neutral-500">{actions.promoteHint}</span>}
          </div>
          {confirm === "promote" && (
            <ConfirmPanel
              title={`¿Promover ${label} a activa?`}
              description="Empezará a operar su sleeve con capital en los bots de todos los usuarios, según el nivel de riesgo de cada uno."
              confirmLabel="Sí, promover"
              busy={busy}
              error={error}
              reason={{ label: "Motivo", initial: `${(strategy.shadow_days_clean ?? 0).toFixed(0)} días en shadow sin fallas` }}
              onConfirm={handleConfirm}
              onCancel={() => setConfirm(null)}
            />
          )}
          {confirm === "retire" && (
            <ConfirmPanel
              title={`¿Retirar ${label}?`}
              description="Deja de recibir capital y se archiva con sus reportes. Para volver a usarla tendrá que revalidarse en el laboratorio."
              confirmLabel="Sí, retirar"
              tone="danger"
              busy={busy}
              error={error}
              reason={{ label: "Motivo", placeholder: "Por qué se retira" }}
              onConfirm={handleConfirm}
              onCancel={() => setConfirm(null)}
            />
          )}
        </div>
      )}
    </article>
  );
}
