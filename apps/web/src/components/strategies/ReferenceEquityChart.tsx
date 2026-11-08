"use client";

import { Area, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { ReferencePoint } from "@/lib/strategies/data";
import { formatDay, formatMoney } from "@/lib/format";

const LINE = "#059669"; // emerald-600: equity real
const BAND = "#10b981"; // emerald-500 con opacidad: banda p5–p95
const MEDIAN = "#737373"; // neutral-500: mediana esperada
const AXIS = "#a3a3a3";

function dayOf(t: number) {
  return new Date(t).toISOString().slice(0, 10);
}

/**
 * Equity del portafolio de referencia (100 % del sleeve) contra la banda p5–p95 que
 * esperaba el Monte Carlo del laboratorio. Sin banda (reporte sin `final_return`) se
 * dibuja solo la curva.
 */
export function ReferenceEquityChart({ points, name }: { points: ReferencePoint[]; name: string }) {
  const hasBand = points.some((p) => p.band);
  return (
    <div className="h-56 w-full" role="img" aria-label={`Equity de referencia de ${name} contra la banda del Monte Carlo`}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={AXIS} strokeOpacity={0.25} vertical={false} />
          <XAxis
            dataKey="t"
            type="number"
            scale="time"
            domain={["dataMin", "dataMax"]}
            tickFormatter={(t: number) => formatDay(dayOf(t))}
            stroke={AXIS}
            fontSize={12}
            tickLine={false}
            minTickGap={32}
          />
          <YAxis
            stroke={AXIS}
            fontSize={12}
            tickLine={false}
            axisLine={false}
            width={72}
            domain={["auto", "auto"]}
            tickFormatter={(v: number) => formatMoney(v, 0)}
          />
          <Tooltip
            labelFormatter={(t) => formatDay(dayOf(Number(t)))}
            formatter={(v, label) => {
              const text = Array.isArray(v)
                ? `${formatMoney(Number(v[0]), 0)} – ${formatMoney(Number(v[1]), 0)}`
                : formatMoney(Number(v), 0);
              return [`${text} USDT`, label];
            }}
          />
          {hasBand && <Legend verticalAlign="top" height={28} iconSize={10} wrapperStyle={{ fontSize: 12 }} />}
          {hasBand && (
            <Area
              dataKey="band"
              name="Banda esperada (p5–p95)"
              stroke="none"
              fill={BAND}
              fillOpacity={0.18}
              isAnimationActive={false}
            />
          )}
          {hasBand && (
            <Line
              dataKey="p50"
              name="Mediana esperada"
              stroke={MEDIAN}
              strokeDasharray="4 4"
              strokeWidth={1.5}
              dot={false}
              isAnimationActive={false}
            />
          )}
          <Line
            dataKey="equity"
            name="Equity de referencia"
            stroke={LINE}
            strokeWidth={2}
            dot={points.length < 3}
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
