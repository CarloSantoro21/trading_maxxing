"use client";

import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { EquityPoint } from "@/lib/dashboard/data";
import { formatDateTime, formatMoney } from "@/lib/format";

const LINE = "#059669"; // emerald-600
const AXIS = "#a3a3a3"; // neutral-400

function shortDate(t: number) {
  return new Date(t).toLocaleDateString("es-MX", { day: "numeric", month: "short" });
}

/** Curva de equity del portafolio (una serie) con el capital inicial como referencia. */
export function EquityChart({ points, capital }: { points: EquityPoint[]; capital: number }) {
  return (
    <figure>
      <div className="h-64 w-full sm:h-72" role="img" aria-label="Gráfica de equity del portafolio en el tiempo">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid stroke={AXIS} strokeOpacity={0.25} vertical={false} />
            <XAxis
              dataKey="t"
              type="number"
              scale="time"
              domain={["dataMin", "dataMax"]}
              tickFormatter={shortDate}
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
            <ReferenceLine y={capital} stroke={AXIS} strokeDasharray="4 4" />
            <Tooltip
              labelFormatter={(t) => formatDateTime(new Date(Number(t)).toISOString())}
              formatter={(v) => [`${formatMoney(Number(v))} USDT`, "Equity"]}
            />
            <Line
              type="monotone"
              dataKey="equity"
              name="Equity"
              stroke={LINE}
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="mt-2 text-xs text-neutral-500">
        Línea punteada: capital inicial ({formatMoney(capital)} USDT).
      </figcaption>
    </figure>
  );
}
