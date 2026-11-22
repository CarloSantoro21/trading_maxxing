"use client";

import useSWR from "swr";
import { useMe } from "@/components/auth/RequireAuth";
import { POLL_MS } from "@/components/providers/ApiProvider";
import { StrategyCard } from "@/components/strategies/StrategyCard";
import { EmptyState, ErrorBox, LiveBadge, Loading } from "@/components/ui/feedback";
import { apiErrorMessage } from "@/lib/api/client";
import { fetchStrategies, STRATEGIES_KEY, type StrategiesData } from "@/lib/strategies/data";

/**
 * Página de estrategias (#58): catálogo compartido con su estado, su portafolio de
 * referencia contra la banda del Monte Carlo, el reporte del laboratorio y el historial.
 * Los admins ven además los botones de promover y retirar, que llaman a la API.
 *
 * Lee `GET /api/strategies/overview` y la vuelve a pedir cada `refreshMs` (polling con SWR,
 * pausado con la pestaña oculta).
 */
export function StrategiesView({ refreshMs = POLL_MS }: { refreshMs?: number }) {
  const { me } = useMe();
  const { data, error, isLoading, mutate } = useSWR<StrategiesData>(STRATEGIES_KEY, fetchStrategies, {
    refreshInterval: refreshMs,
  });

  const header = (
    <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 className="text-2xl font-semibold">Estrategias</h1>
        <p className="mt-1 max-w-2xl text-sm text-neutral-600 dark:text-neutral-300">
          Las estrategias son de toda la app: el laboratorio, las señales y el ciclo de vida se calculan una vez para
          todos. Cada bot las ejecuta según su capital y su nivel de riesgo.
        </p>
      </div>
      <div className="flex items-center gap-2">
        {data?.isAdmin && (
          <span className="rounded-full bg-neutral-900 px-2.5 py-0.5 text-xs font-semibold text-white dark:bg-neutral-100 dark:text-neutral-900">
            Admin
          </span>
        )}
        <LiveBadge live={Boolean(data) && !error} />
      </div>
    </div>
  );

  if (isLoading && !data) {
    return (
      <>
        {header}
        <Loading>Cargando estrategias…</Loading>
      </>
    );
  }
  if (!data) {
    return (
      <>
        {header}
        <ErrorBox onRetry={() => void mutate()}>
          No se pudieron cargar las estrategias. {apiErrorMessage(error)}
        </ErrorBox>
      </>
    );
  }
  return (
    <>
      {header}
      {data.strategies.length === 0 ? (
        <EmptyState title="No hay estrategias registradas">
          Se registran al arrancar el worker de agentes (StrategyRegistry).
        </EmptyState>
      ) : (
        <div className="space-y-6">
          {data.strategies.map((s) => (
            <StrategyCard
              key={s.id}
              strategy={s}
              equity={data.equity}
              transitions={data.transitions}
              isAdmin={data.isAdmin}
              currentUserId={me.id}
              onTransition={() => void mutate()}
            />
          ))}
        </div>
      )}
    </>
  );
}
