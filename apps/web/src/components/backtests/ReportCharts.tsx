"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { AXIS, CRITICAL, GRID, SERIES_1, SERIES_2, axisProps, tooltipStyle, vizRootClass } from "@/components/charts/theme";
import type { DrawdownHistogram, LabReportDetail } from "@/lib/backtests/api";
import { downsample, histogramBars, normalizeEquity, num, pct } from "@/lib/backtests/format";

/** Curva de equity out-of-sample contra comprar y mantener (crecimiento de 1 USDT). */
export function EquityChart({ equity }: { equity: LabReportDetail["equity"] }) {
  const data = downsample(normalizeEquity(equity));
  if (data.length === 0) return <p className="text-sm text-neutral-500">El reporte no trae curva de equity.</p>;
  const last = data[data.length - 1];
  return (
    <figure className={vizRootClass} aria-label="Curva de equity out-of-sample contra comprar y mantener">
      <div className="h-72 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis dataKey="day" {...axisProps} minTickGap={48} />
            <YAxis {...axisProps} width={48} tickFormatter={(v: number) => `${v.toFixed(1)}x`} />
            <Tooltip
              {...tooltipStyle}
              formatter={(v) => (typeof v === "number" ? `${v.toFixed(2)}x` : String(v))}
            />
            <Legend />
            <Line name="Estrategia" dataKey="strategy" stroke={SERIES_1} strokeWidth={2} dot={false} />
            <Line
              name="Comprar y mantener"
              dataKey="buy_and_hold"
              stroke={SERIES_2}
              strokeWidth={2}
              dot={false}
              connectNulls
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="mt-2 text-sm text-neutral-600 dark:text-neutral-300">
        Crecimiento de 1 USDT. Al final: estrategia {num(last.strategy)}x, comprar y mantener{" "}
        {num(last.buy_and_hold)}x.
      </figcaption>
    </figure>
  );
}

/** Histograma del drawdown máximo del Monte Carlo, con el p95 y el tope del criterio. */
export function DrawdownHistogramChart({ histogram }: { histogram: DrawdownHistogram }) {
  const bars = histogramBars(histogram.bins, histogram.p95);
  const p95 = histogram.p95 * 100;
  const limit = histogram.limit !== null ? histogram.limit * 100 : null;
  return (
    <figure className={vizRootClass} aria-label="Histograma del drawdown máximo del Monte Carlo">
      <div className="h-64 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={bars} margin={{ top: 16, right: 16, bottom: 0, left: 0 }} barCategoryGap={2}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis
              dataKey="mid"
              type="number"
              domain={[0, "dataMax"]}
              {...axisProps}
              tickFormatter={(v: number) => `${v.toFixed(0)} %`}
            />
            <YAxis {...axisProps} width={48} />
            <Tooltip
              {...tooltipStyle}
              labelFormatter={(_, payload) => String(payload?.[0]?.payload?.label ?? "")}
              formatter={(v) => [`${v} simulaciones`, "Frecuencia"]}
            />
            <Bar dataKey="count" radius={[4, 4, 0, 0]}>
              {bars.map((b) => (
                <Cell key={b.mid} fill={SERIES_1} fillOpacity={b.tail ? 1 : 0.55} />
              ))}
            </Bar>
            <ReferenceLine
              x={p95}
              stroke={AXIS}
              strokeDasharray="4 4"
              label={{ value: `p95 ${p95.toFixed(1)} %`, position: "top", fill: AXIS, fontSize: 12 }}
            />
            {limit !== null && (
              <ReferenceLine
                x={limit}
                stroke={CRITICAL}
                label={{ value: `tope ${limit.toFixed(0)} %`, position: "insideTopRight", fill: AXIS, fontSize: 12 }}
              />
            )}
          </BarChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="mt-2 text-sm text-neutral-600 dark:text-neutral-300">
        {histogram.n_simulations.toLocaleString("es-MX")} simulaciones de {histogram.horizon_days} días
        (block bootstrap de {histogram.block_size} días, semilla {histogram.seed}). Mediana{" "}
        {pct(histogram.p50)}, p95 {pct(histogram.p95)}
        {histogram.limit !== null ? `, tope del criterio ${pct(histogram.limit)}` : ""}. Las barras
        sólidas son el 5 % peor.
      </figcaption>
    </figure>
  );
}
