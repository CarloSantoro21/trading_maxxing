"use client";

import Link from "next/link";
import { useCallback, useMemo, useState } from "react";
import useSWR from "swr";
import { BotControls } from "@/components/dashboard/BotControls";
import { EquityChart } from "@/components/dashboard/EquityChart";
import { SleevePositions } from "@/components/dashboard/SleevePositions";
import { TradeList } from "@/components/dashboard/TradeList";
import { POLL_MS } from "@/components/providers/ApiProvider";
import { Card, EmptyState, ErrorBox, LiveBadge, Loading } from "@/components/ui/feedback";
import { primaryButtonClass } from "@/components/ui/styles";
import { apiErrorMessage, createBot, type ApiBot } from "@/lib/api/client";
import {
  DASHBOARD_KEY,
  portfolioEquity,
  sleeves,
  summarize,
  withBot,
  type DashboardData,
} from "@/lib/dashboard/data";
import { formatDateTime, formatMoney, formatPct, formatSignedMoney } from "@/lib/format";

function Stat({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: "good" | "bad" }) {
  return (
    <div className="rounded-md border border-neutral-200 p-3 dark:border-neutral-800">
      <dt className="text-xs text-neutral-500">{label}</dt>
      <dd
        className={`mt-1 text-xl font-semibold tabular-nums ${
          tone === "good" ? "text-emerald-700 dark:text-emerald-400" : tone === "bad" ? "text-red-700 dark:text-red-400" : ""
        }`}
      >
        {value}
      </dd>
      {hint && <dd className="mt-0.5 text-xs text-neutral-500">{hint}</dd>}
    </div>
  );
}

const tone = (v: number) => (v > 0 ? "good" : v < 0 ? "bad" : undefined);

/**
 * Dashboard en tiempo real (#23): equity, P&L, posiciones por sleeve, últimas operaciones
 * con su rationale y los controles del bot. Lee `GET /api/dashboard` y lo vuelve a pedir
 * cada `refreshMs` (polling con SWR, que se pausa con la pestaña oculta). Las respuestas de
 * los botones del bot se aplican al momento sin esperar al siguiente refresco.
 * Va dentro de <RequireAuth>.
 */
export function DashboardView({ refreshMs = POLL_MS }: { refreshMs?: number }) {
  const { data: loaded, error, isLoading, mutate } = useSWR<DashboardData>(DASHBOARD_KEY, {
    refreshInterval: refreshMs,
  });
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const applyBot = useCallback(
    (bot: ApiBot) => void mutate((current) => (current ? withBot(current, bot) : current), { revalidate: false }),
    [mutate],
  );
  const summary = useMemo(() => (loaded ? summarize(loaded) : null), [loaded]);
  const curve = useMemo(() => portfolioEquity(loaded?.snapshots ?? []), [loaded]);
  const bySleeve = useMemo(() => (loaded ? sleeves(loaded) : []), [loaded]);

  async function handleCreateBot() {
    setCreating(true);
    setCreateError(null);
    try {
      applyBot(await createBot("Mi bot"));
    } catch (err) {
      setCreateError(apiErrorMessage(err));
    } finally {
      setCreating(false);
    }
  }

  const header = (
    <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 className="text-2xl font-semibold">Mi portafolio</h1>
        <p className="mt-1 text-sm text-neutral-600 dark:text-neutral-300">
          BTC/USDT y ETH/USDT en Binance Testnet (sin dinero real).
        </p>
      </div>
      <LiveBadge live={Boolean(loaded) && !error} />
    </div>
  );

  if (isLoading && !loaded) {
    return (
      <>
        {header}
        <Loading>Cargando tu portafolio…</Loading>
      </>
    );
  }
  if (!loaded || !summary) {
    return (
      <>
        {header}
        <ErrorBox onRetry={() => void mutate()}>No se pudo cargar el dashboard. {apiErrorMessage(error)}</ErrorBox>
      </>
    );
  }
  const data = loaded;
  if (!data.profile) {
    return (
      <>
        {header}
        <EmptyState title="Todavía no configuras tu perfil">
          <p>Elige tu capital, tu nivel de riesgo y tus metas para que el bot sepa cómo operar.</p>
          <Link href="/onboarding" className={`${primaryButtonClass} mt-3`}>
            Configurar mi perfil
          </Link>
        </EmptyState>
      </>
    );
  }

  return (
    <>
      {header}
      <dl className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat
          label="Equity"
          value={`${formatMoney(summary.equity)} USDT`}
          hint={summary.asOf ? `al ${formatDateTime(summary.asOf)}` : "sin datos del bot todavía"}
        />
        <Stat
          label="P&L total"
          value={`${formatSignedMoney(summary.pnl)} USDT`}
          hint={formatPct(summary.totalReturn, 2, true)}
          tone={tone(summary.pnl)}
        />
        <Stat
          label="P&L realizado"
          value={`${formatSignedMoney(summary.realizedPnl)} USDT`}
          hint="de operaciones cerradas"
          tone={tone(summary.realizedPnl)}
        />
        <Stat
          label="Caída desde el máximo"
          value={formatPct(summary.drawdown)}
          hint={
            data.profile.max_drawdown !== null
              ? `tu límite (nivel ${data.profile.risk_level}): ${formatPct(data.profile.max_drawdown, 0)}`
              : undefined
          }
          tone={summary.drawdown > 0 ? "bad" : undefined}
        />
      </dl>

      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        <Card title="Equity" className="lg:col-span-2">
          {curve.length < 2 ? (
            <EmptyState title="Aún no hay curva de equity">
              El bot guarda una foto de tu equity mientras opera; la gráfica aparece con dos o más puntos.
            </EmptyState>
          ) : (
            <EquityChart points={curve} capital={summary.capital} />
          )}
        </Card>

        <Card title="Bot">
          {data.bots.length === 0 ? (
            <EmptyState title="Aún no tienes bot">
              <p>Tu bot ejecuta las señales de las estrategias activas según tu capital y tu nivel de riesgo.</p>
              <button type="button" className={`${primaryButtonClass} mt-3`} disabled={creating} onClick={handleCreateBot}>
                {creating ? "Creando…" : "Crear mi bot"}
              </button>
              {createError && (
                <p role="alert" className="mt-2 text-red-700 dark:text-red-400">
                  {createError}
                </p>
              )}
            </EmptyState>
          ) : (
            <div className="space-y-6">
              {data.bots.map((bot) => (
                <BotControls key={bot.id} bot={bot} onChange={applyBot} />
              ))}
            </div>
          )}
        </Card>
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <Card title="Posiciones por estrategia">
          <SleevePositions sleeves={bySleeve} />
        </Card>
        <Card title="Últimas operaciones">
          <TradeList trades={data.recentTrades} />
        </Card>
      </div>
    </>
  );
}
