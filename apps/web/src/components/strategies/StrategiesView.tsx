"use client";

import { useEffect, useRef, useState } from "react";
import { useAuth } from "@/components/auth/AuthProvider";
import { StrategyCard } from "@/components/strategies/StrategyCard";
import { EmptyState, ErrorBox, LiveBadge, Loading } from "@/components/ui/feedback";
import { fetchStrategies, StrategiesLoadError, type StrategiesData } from "@/lib/strategies/data";

/**
 * Página de estrategias (#58): catálogo compartido con su estado, su portafolio de
 * referencia contra la banda del Monte Carlo, el reporte del laboratorio y el historial.
 * Los admins (app_roles) ven además los botones de promover y retirar, que llaman a la API.
 *
 * Escucha strategies y reference_equity por Realtime; cada cambio vuelve a leer todo
 * (son pocas filas) tras `refreshDelayMs`, para agrupar ráfagas de upserts del ledger.
 */
export function StrategiesView({ refreshDelayMs = 1000 }: { refreshDelayMs?: number }) {
  const { supabase, session } = useAuth();
  const userId = session?.user.id;
  const token = session?.access_token ?? "";
  const [data, setData] = useState<StrategiesData | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState(false);
  const [version, setVersion] = useState(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!supabase || !userId) return;
    let active = true;
    fetchStrategies(supabase, userId)
      .then((loaded) => {
        if (!active) return;
        setData(loaded);
        setStatus("ready");
      })
      .catch((err) => {
        if (!active) return;
        setError(err instanceof StrategiesLoadError ? err.message : "No se pudieron cargar las estrategias.");
        setStatus("error");
      });
    return () => {
      active = false;
    };
  }, [supabase, userId, version]);

  useEffect(() => {
    if (!supabase || !userId) return;
    const schedule = () => {
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setVersion((v) => v + 1), refreshDelayMs);
    };
    const channel = supabase
      .channel(`strategies:${userId}`)
      .on("postgres_changes", { event: "*", schema: "public", table: "strategies" }, schedule)
      .on("postgres_changes", { event: "*", schema: "public", table: "reference_equity" }, schedule)
      .subscribe((state) => setLive(state === "SUBSCRIBED"));
    return () => {
      if (timer.current) clearTimeout(timer.current);
      setLive(false);
      void supabase.removeChannel(channel);
    };
  }, [supabase, userId, refreshDelayMs]);

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
        <LiveBadge live={live} />
      </div>
    </div>
  );

  if (status === "loading") {
    return (
      <>
        {header}
        <Loading>Cargando estrategias…</Loading>
      </>
    );
  }
  if (status === "error" || !data) {
    return (
      <>
        {header}
        <ErrorBox
          onRetry={() => {
            setStatus("loading");
            setVersion((v) => v + 1);
          }}
        >
          {error}
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
              token={token}
              currentUserId={userId}
              onTransition={() => setVersion((v) => v + 1)}
            />
          ))}
        </div>
      )}
    </>
  );
}
