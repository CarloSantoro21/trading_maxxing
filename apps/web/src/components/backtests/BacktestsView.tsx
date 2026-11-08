"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/components/auth/AuthProvider";
import { SectionNav } from "@/components/nav/SectionNav";
import { cardClass, secondaryButtonClass } from "@/components/ui/styles";
import {
  getLabReport,
  listBacktests,
  listLabReports,
  type BacktestJob,
  type LabReportDetail,
  type LabReportSummary,
} from "@/lib/backtests/api";
import { pct } from "@/lib/backtests/format";
import type { Fetcher } from "@/lib/tmApi";
import { BacktestForm } from "./BacktestForm";
import { ReportDetail, VerdictBadge } from "./ReportDetail";

const STATUS_LABEL: Record<BacktestJob["status"], string> = {
  queued: "En cola",
  running: "Corriendo",
  done: "Terminado",
  failed: "Falló",
};

/** Lee `?report=` de la URL (el sitio es estático: no hay rutas dinámicas). */
function reportFromUrl(): string | null {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.search).get("report");
}

function setReportInUrl(name: string | null) {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  if (name) url.searchParams.set("report", name);
  else url.searchParams.delete("report");
  window.history.replaceState(null, "", url.toString());
}

function ReportsTable({
  reports,
  onSelect,
}: {
  reports: LabReportSummary[];
  onSelect: (name: string) => void;
}) {
  if (reports.length === 0) {
    return <p className="text-sm text-neutral-500">Todavía no hay reportes del laboratorio.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-sm">
        <caption className="sr-only">Reportes del laboratorio de walk-forward</caption>
        <thead>
          <tr className="text-left text-neutral-500">
            <th scope="col" className="py-1 pr-2 font-medium">Reporte</th>
            <th scope="col" className="py-1 pr-2 font-medium">Out-of-sample</th>
            <th scope="col" className="py-1 pr-2 text-right font-medium">Retorno</th>
            <th scope="col" className="py-1 pr-2 text-right font-medium">Comprar y mantener</th>
            <th scope="col" className="py-1 pr-2 text-right font-medium">DD p95</th>
            <th scope="col" className="py-1 font-medium">Veredicto</th>
          </tr>
        </thead>
        <tbody className="tabular-nums">
          {reports.map((r) => (
            <tr key={r.name} className="border-t border-neutral-100 dark:border-neutral-800">
              <th scope="row" className="py-2 pr-2 text-left font-normal">
                <button
                  type="button"
                  className="font-mono text-emerald-700 underline underline-offset-4 dark:text-emerald-400"
                  onClick={() => onSelect(r.name)}
                >
                  {r.name}
                </button>
                <span className="ml-2 text-neutral-500">{r.symbols.join(", ")}</span>
              </th>
              <td className="py-2 pr-2">
                {r.oos_start} → {r.oos_end}
              </td>
              <td className="py-2 pr-2 text-right">{pct(r.aggregate.total_return)}</td>
              <td className="py-2 pr-2 text-right">{pct(r.buy_and_hold.total_return)}</td>
              <td className="py-2 pr-2 text-right">{pct(r.max_drawdown_p95)}</td>
              <td className="py-2">
                <VerdictBadge approved={r.approved} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function JobsList({ jobs }: { jobs: BacktestJob[] }) {
  if (jobs.length === 0) return <p className="text-sm text-neutral-500">No has encolado backtests.</p>;
  return (
    <ul className="divide-y divide-neutral-100 text-sm dark:divide-neutral-800">
      {jobs.map((j) => {
        const symbols = Array.isArray(j.config.symbols) ? (j.config.symbols as string[]).join(", ") : "";
        return (
          <li key={j.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
            <span>
              <span className="font-mono">{j.id.slice(0, 8)}</span> · {symbols} · {String(j.config.start ?? "")} →{" "}
              {String(j.config.end ?? "")}
            </span>
            <span className={j.status === "failed" ? "text-red-700 dark:text-red-400" : "text-neutral-600 dark:text-neutral-300"}>
              {STATUS_LABEL[j.status]}
              {j.error ? `: ${j.error}` : ""}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

/** Página /backtests: reportes del laboratorio, su detalle y los backtests del usuario. */
export function BacktestsView({ fetcher }: { fetcher?: Fetcher }) {
  const { session } = useAuth();
  const token = session?.access_token ?? "";
  const [reports, setReports] = useState<LabReportSummary[] | null>(null);
  const [jobs, setJobs] = useState<BacktestJob[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<LabReportDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  useEffect(() => {
    setSelected(reportFromUrl());
  }, []);

  const loadJobs = useCallback(() => {
    if (!token) return;
    listBacktests(token, fetcher)
      .then(setJobs)
      .catch(() => setJobs([]));
  }, [token, fetcher]);

  useEffect(() => {
    if (!token) return;
    let active = true;
    listLabReports(token, fetcher)
      .then((r) => active && setReports(r))
      .catch((err) => active && setError(err instanceof Error ? err.message : String(err)));
    loadJobs();
    return () => {
      active = false;
    };
  }, [token, fetcher, loadJobs]);

  useEffect(() => {
    if (!token || !selected) {
      setDetail(null);
      return;
    }
    let active = true;
    setDetail(null);
    setDetailError(null);
    getLabReport(token, selected, fetcher)
      .then((d) => active && setDetail(d))
      .catch((err) => active && setDetailError(err instanceof Error ? err.message : String(err)));
    return () => {
      active = false;
    };
  }, [token, selected, fetcher]);

  function select(name: string | null) {
    setSelected(name);
    setReportInUrl(name);
  }

  if (selected) {
    return (
      <main className="mx-auto w-full max-w-5xl px-4 py-10">
        <SectionNav current="/backtests" />
        <button type="button" className={`${secondaryButtonClass} mb-6`} onClick={() => select(null)}>
          ← Todos los reportes
        </button>
        {detailError && (
          <p role="alert" className="text-red-700 dark:text-red-400">
            {detailError}
          </p>
        )}
        {!detail && !detailError && (
          <p role="status" className="text-neutral-500">
            Cargando el reporte…
          </p>
        )}
        {detail && <ReportDetail report={detail} />}
      </main>
    );
  }

  return (
    <main className="mx-auto w-full max-w-5xl space-y-6 px-4 py-10">
      <SectionNav current="/backtests" />
      <header>
        <h1 className="text-2xl font-semibold">Backtests y reportes</h1>
        <p className="mt-1 text-sm text-neutral-600 dark:text-neutral-300">
          Resultados out-of-sample del laboratorio de walk-forward, con comisión de 0.1 % y Monte Carlo.
        </p>
      </header>

      <section className={cardClass} aria-labelledby="reports-title">
        <h2 id="reports-title" className="mb-3 text-lg font-semibold">
          Reportes del laboratorio
        </h2>
        {error && (
          <p role="alert" className="text-red-700 dark:text-red-400">
            {error}
          </p>
        )}
        {!error && reports === null && (
          <p role="status" className="text-neutral-500">
            Cargando reportes…
          </p>
        )}
        {reports && <ReportsTable reports={reports} onSelect={select} />}
      </section>

      <section className={cardClass} aria-labelledby="enqueue-title">
        <h2 id="enqueue-title" className="mb-3 text-lg font-semibold">
          Encolar un backtest
        </h2>
        {token && <BacktestForm token={token} fetcher={fetcher} onCreated={loadJobs} />}
      </section>

      <section className={cardClass} aria-labelledby="jobs-title">
        <h2 id="jobs-title" className="mb-3 text-lg font-semibold">
          Mis backtests
        </h2>
        <JobsList jobs={jobs} />
      </section>
    </main>
  );
}
