"use client";

import {
  Area,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { AXIS, GRID, SERIES_1, axisProps, tooltipStyle, vizRootClass } from "@/components/charts/theme";
import { downsample, usd } from "@/lib/backtests/format";
import { fanSeries, type GoalProbability } from "@/lib/goals/probability";

/** Abanico p10/p50/p90 del equity proyectado, con la línea de la meta. */
export function GoalFanChart({ projection }: { projection: GoalProbability }) {
  const data = downsample(fanSeries(projection.fan), 200);
  if (data.length === 0) return <p className="text-sm text-neutral-500">No hay proyección para dibujar.</p>;
  return (
    <figure className={vizRootClass} aria-label="Abanico del equity proyectado con p10, p50 y p90">
      <div className="h-72 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 16, right: 16, bottom: 0, left: 8 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis dataKey="day" {...axisProps} minTickGap={48} />
            <YAxis
              {...axisProps}
              width={72}
              domain={["auto", "auto"]}
              tickFormatter={(v: number) => v.toLocaleString("es-MX", { maximumFractionDigits: 0 })}
            />
            <Tooltip
              {...tooltipStyle}
              formatter={(v, name) =>
                Array.isArray(v) ? [`${usd(v[0])} – ${usd(v[1])}`, name] : [usd(Number(v)), name]
              }
            />
            <Legend />
            <Area
              name="p10–p90"
              dataKey="band"
              stroke="none"
              fill={SERIES_1}
              fillOpacity={0.18}
              isAnimationActive={false}
            />
            <Line name="Mediana (p50)" dataKey="p50" stroke={SERIES_1} strokeWidth={2} dot={false} />
            <ReferenceLine
              y={projection.target_equity}
              stroke={AXIS}
              strokeDasharray="4 4"
              label={{ value: `meta ${usd(projection.target_equity)}`, position: "insideTopLeft", fill: AXIS, fontSize: 12 }}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="mt-2 text-sm text-neutral-600 dark:text-neutral-300">
        {projection.n_simulations.toLocaleString("es-MX")} trayectorias (block bootstrap de{" "}
        {projection.block_size} días, semilla {projection.seed}). Al {projection.deadline}: p10{" "}
        {usd(projection.final_equity_p10)}, p50 {usd(projection.final_equity_p50)}, p90{" "}
        {usd(projection.final_equity_p90)}.
      </figcaption>
    </figure>
  );
}
