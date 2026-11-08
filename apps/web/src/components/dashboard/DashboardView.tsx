"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useReducer, useState } from "react";
import { useAuth } from "@/components/auth/AuthProvider";
import { BotControls } from "@/components/dashboard/BotControls";
import { EquityChart } from "@/components/dashboard/EquityChart";
import { SleevePositions } from "@/components/dashboard/SleevePositions";
import { TradeList } from "@/components/dashboard/TradeList";
import { Card, EmptyState, ErrorBox, LiveBadge, Loading } from "@/components/ui/feedback";
import { primaryButtonClass } from "@/components/ui/styles";
import { apiErrorMessage, createBot, type ApiBot } from "@/lib/api/client";
import {
  dashboardReducer,
  DashboardLoadError,
  EMPTY_DASHBOARD,
  fetchDashboard,
  portfolioEquity,
  sleeves,
  summarize,
  type BotRow,
  type SnapshotRow,
  type TradeRow,
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
 * con su rationale y los controles del bot. Carga con supabase-js y después escucha
 * trades, equity_snapshots y bots por Supabase Realtime (RLS filtra los eventos ajenos).
 * Va dentro de <RequireAuth>.
 */
export function DashboardView() {
  const { supabase, session } = useAuth();
  const userId = session?.user.id;
  const token = session?.access_token ?? "";
  const [data, dispatch] = useReducer(dashboardReducer, EMPTY_DASHBOARD);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState(false);
  const [reload, setReload] = useState(0);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  useEffect(() => {
    if (!supabase || !userId) return;
    let active = true;
    setStatus("loading");
    fetchDashboard(supabase, userId)
      .then((loaded) => {
        if (!active) return;
        dispatch({ type: "loaded", data: loaded });
        setStatus("ready");
      })
      .catch((err) => {
        if (!active) return;
        setError(err instanceof DashboardLoadError ? err.message : "No se pudo cargar el dashboard.");
        setStatus("error");
      });
    return () => {
      active = false;
    };
  }, [supabase, userId, reload]);

  useEffect(() => {
    if (!supabase || !userId) return;
    const channel = supabase
      .channel(`dashboard:${userId}`)
      .on("postgres_changes", { event: "*", schema: "public", table: "trades" }, (payload) => {
        if (payload.eventType !== "DELETE") dispatch({ type: "trade", row: payload.new as TradeRow });
      })
      .on("postgres_changes", { event: "INSERT", schema: "public", table: "equity_snapshots" }, (payload) => {
        dispatch({ type: "snapshot", row: payload.new as SnapshotRow });
      })
      .on("postgres_changes", { event: "*", schema: "public", table: "bots" }, (payload) => {
        if (payload.eventType === "DELETE") {
          const id = (payload.old as { id?: string }).id;
          if (id) dispatch({ type: "botDeleted", id });
        } else {
          dispatch({ type: "bot", row: payload.new as BotRow });
        }
      })
      .subscribe((state) => setLive(state === "SUBSCRIBED"));
    return () => {
      setLive(false);
      void supabase.removeChannel(channel);
    };
  }, [supabase, userId]);

  const onBotChange = useCallback((bot: ApiBot) => dispatch({ type: "bot", row: bot }), []);
  const summary = useMemo(() => summarize(data), [data]);
  const curve = useMemo(() => portfolioEquity(data.snapshots), [data.snapshots]);
  const bySleeve = useMemo(() => sleeves(data), [data]);

  async function handleCreateBot() {
    setCreating(true);
    setCreateError(null);
    try {
      const bot = await createBot("Mi bot", { token });
      dispatch({ type: "bot", row: bot });
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
      <LiveBadge live={live} />
    </div>
  );

  if (status === "loading") {
    return (
      <>
        {header}
        <Loading>Cargando tu portafolio…</Loading>
      </>
    );
  }
  if (status === "error") {
    return (
      <>
        {header}
        <ErrorBox onRetry={() => setReload((n) => n + 1)}>{error}</ErrorBox>
      </>
    );
  }
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
                <BotControls key={bot.id} bot={bot} token={token} onChange={onBotChange} />
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
