"use client";

import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";
import { cardClass, inputClass, labelClass } from "@/components/ui/styles";
import { apiErrorMessage } from "@/lib/api/client";
import { pct, usd } from "@/lib/backtests/format";
import {
  GOALS_KEY,
  SOURCE_LABEL,
  goalProbabilityKey,
  probabilityLabel,
  type GoalProbability,
  type GoalRow,
} from "@/lib/goals/probability";
import { GoalFanChart } from "./GoalFanChart";

function ProbabilityCard({ projection }: { projection: GoalProbability }) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
        <p>
          <span className="block text-sm text-neutral-500">Probabilidad de alcanzar la meta antes del {projection.deadline}</span>
          <span className="text-3xl font-semibold tabular-nums">{pct(projection.probability, 0)}</span>{" "}
          <span className="text-sm text-neutral-600 dark:text-neutral-300">{probabilityLabel(projection.probability)}</span>
        </p>
        <p className="text-sm text-neutral-600 dark:text-neutral-300">
          Capital {usd(projection.start_equity)} → meta {usd(projection.target_equity)} (
          {pct(projection.target_return)}) en {projection.days} días. Fracción invertida{" "}
          {pct(projection.invested_fraction, 0)}.
        </p>
      </div>
      <GoalFanChart projection={projection} />
      {projection.strategies.length > 0 && (
        <table className="w-full text-sm">
          <caption className="sr-only">Estrategias usadas en la proyección</caption>
          <thead>
            <tr className="text-left text-neutral-500">
              <th scope="col" className="py-1 font-medium">Estrategia</th>
              <th scope="col" className="py-1 font-medium">Fuente</th>
              <th scope="col" className="py-1 text-right font-medium">Peso</th>
              <th scope="col" className="py-1 text-right font-medium">Exposición</th>
            </tr>
          </thead>
          <tbody className="tabular-nums">
            {projection.strategies.map((s) => (
              <tr key={s.name} className="border-t border-neutral-100 dark:border-neutral-800">
                <th scope="row" className="py-1 text-left font-normal">
                  {s.name}
                  {s.excluded && <span className="ml-2 text-neutral-500">({s.excluded})</span>}
                </th>
                <td className="py-1">{SOURCE_LABEL[s.source]}</td>
                <td className="py-1 text-right">{pct(s.weight, 0)}</td>
                <td className="py-1 text-right">{pct(s.exposure, 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {projection.notes.length > 0 && (
        <ul className="list-disc pl-5 text-sm text-neutral-600 dark:text-neutral-300">
          {projection.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}
      <p className="text-xs text-neutral-500">
        Es una proyección con datos históricos, no una promesa de rendimiento.
      </p>
    </div>
  );
}

/** Página /goals: elige una meta y muestra su probabilidad (Monte Carlo #3). */
export function GoalsView() {
  const [chosen, setChosen] = useState<string>("");
  const { data: goals, error: goalsError } = useSWR<GoalRow[]>(GOALS_KEY);
  const goalId = chosen || goals?.[0]?.id || "";
  const {
    data: projection,
    error: projectionError,
    isLoading: loading,
  } = useSWR<GoalProbability>(goalId ? goalProbabilityKey(goalId) : null, { revalidateOnFocus: false });
  const failure = goalsError ?? projectionError;
  const error = failure ? apiErrorMessage(failure) : null;

  return (
    <div className="mx-auto w-full max-w-4xl space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">Probabilidad de alcanzar tu meta</h1>
        <p className="mt-1 text-sm text-neutral-600 dark:text-neutral-300">
          Monte Carlo con el portafolio de referencia de tus estrategias activas, escalado por tu nivel de riesgo.
        </p>
      </header>

      <section className={cardClass} aria-labelledby="goal-title">
        <h2 id="goal-title" className="sr-only">
          Proyección de la meta
        </h2>
        {goals === undefined && !error && (
          <p role="status" className="text-neutral-500">
            Cargando tus metas…
          </p>
        )}
        {goals && goals.length === 0 && (
          <p className="text-sm text-neutral-500">
            No tienes metas. Agrégalas en{" "}
            <Link href="/onboarding" className="text-emerald-700 underline underline-offset-4 dark:text-emerald-400">
              Mi perfil
            </Link>
            .
          </p>
        )}
        {goals && goals.length > 1 && (
          <div className="mb-4 max-w-sm">
            <label htmlFor="goal-select" className={labelClass}>
              Meta
            </label>
            <select id="goal-select" className={inputClass} value={goalId} onChange={(e) => setChosen(e.target.value)}>
              {goals.map((g) => (
                <option key={g.id} value={g.id}>
                  {pct(g.target_return)} al {g.deadline}
                </option>
              ))}
            </select>
          </div>
        )}
        {error && (
          <p role="alert" className="text-red-700 dark:text-red-400">
            {error}
          </p>
        )}
        {loading && (
          <p role="status" className="text-neutral-500">
            Simulando trayectorias…
          </p>
        )}
        {projection && <ProbabilityCard projection={projection} />}
      </section>
    </div>
  );
}
