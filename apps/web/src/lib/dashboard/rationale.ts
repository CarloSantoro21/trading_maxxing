import { formatPct, formatSymbol, strategyLabel, toNumber } from "@/lib/format";

/**
 * Explicación en lenguaje claro de `trades.rationale`.
 *
 * El rationale es `tm_shared.contracts.Rationale` serializado (más `sleeve`, que agrega el
 * recorder del engine): señales de cada estrategia, pesos del allocator, veto de noticias,
 * resultado del SimulationGate y veredicto del RiskAgent. Todo es opcional aquí porque la
 * columna es jsonb y puede venir incompleta.
 */

export interface RationaleSignal {
  symbol?: string;
  direction?: "long" | "short" | "flat" | string;
  confidence?: number;
  timeframe?: string;
  source?: string;
}

export interface Rationale {
  sleeve?: string;
  signals?: RationaleSignal[];
  weights?: Record<string, number>;
  news_veto?: boolean;
  news_summary?: string | null;
  sim?: {
    p_take_profit?: number;
    p_stop_loss?: number;
    p_open?: number;
    expected_value?: number;
    n_paths?: number;
    source?: string;
  } | null;
  risk?: { approved?: boolean; reasons?: string[] } | null;
  text?: string | null;
}

export type Tone = "neutral" | "good" | "bad";

export interface RationaleItem {
  /** Sección: Señales, Peso, Noticias, Simulación, Riesgo o Explicación. */
  label: string;
  text: string;
  tone: Tone;
}

const DIRECTION_TEXT: Record<string, string> = {
  long: "recomienda comprar",
  short: "recomienda vender",
  flat: "recomienda quedarse fuera",
};

const SIM_SOURCE_TEXT: Record<string, string> = {
  kronos: "pronóstico de Kronos",
  bootstrap: "historial de precios",
  combined: "Kronos y el historial",
};

export function asRationale(value: unknown): Rationale {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Rationale) : {};
}

function signalText(s: RationaleSignal): string {
  const who = strategyLabel(s.source ?? "estrategia");
  const what = DIRECTION_TEXT[s.direction ?? ""] ?? `da la señal ${s.direction ?? "desconocida"}`;
  const where = s.symbol ? ` ${formatSymbol(s.symbol)}` : "";
  const conf = toNumber(s.confidence);
  const confText = conf === null ? "" : ` con ${formatPct(conf, 0)} de confianza`;
  const tf = s.timeframe ? ` (velas de ${s.timeframe})` : "";
  return `${who} ${what}${where}${confText}${tf}.`;
}

export function describeRationale(raw: unknown): RationaleItem[] {
  const r = asRationale(raw);
  const items: RationaleItem[] = [];

  for (const s of r.signals ?? []) {
    items.push({ label: "Señal", text: signalText(s), tone: "neutral" });
  }

  const weights = Object.entries(r.weights ?? {}).filter(([, w]) => toNumber(w) !== null);
  if (weights.length) {
    const parts = weights.map(([name, w]) => `${strategyLabel(name)} ${formatPct(Number(w), 0)}`);
    items.push({ label: "Peso", text: `Reparto del capital entre estrategias: ${parts.join(", ")}.`, tone: "neutral" });
  }

  if (r.news_veto) {
    items.push({
      label: "Noticias",
      text: `Las noticias bloquearon nuevas compras${r.news_summary ? `: ${r.news_summary}` : "."}`,
      tone: "bad",
    });
  } else if (r.news_summary) {
    items.push({ label: "Noticias", text: `Sin veto. ${r.news_summary}`, tone: "neutral" });
  }

  if (r.sim) {
    const tp = toNumber(r.sim.p_take_profit);
    const sl = toNumber(r.sim.p_stop_loss);
    const ev = toNumber(r.sim.expected_value);
    const n = toNumber(r.sim.n_paths);
    const src = r.sim.source ? (SIM_SOURCE_TEXT[r.sim.source] ?? r.sim.source) : null;
    const parts: string[] = [];
    if (tp !== null && sl !== null) {
      parts.push(`${formatPct(tp, 0)} de probabilidad de llegar al objetivo y ${formatPct(sl, 0)} de tocar el stop`);
    }
    if (ev !== null) {
      parts.push(`ganancia esperada de ${ev >= 0 ? "+" : ""}${ev.toFixed(2)} veces lo arriesgado`);
    }
    const tail = [n !== null ? `${n.toLocaleString("en-US")} escenarios` : null, src].filter(Boolean).join(", ");
    items.push({
      label: "Simulación",
      text: `Antes de operar se simuló el precio: ${parts.join("; ") || "sin datos"}${tail ? ` (${tail})` : ""}.`,
      tone: ev === null ? "neutral" : ev > 0 ? "good" : "bad",
    });
  }

  if (r.risk) {
    const reasons = (r.risk.reasons ?? []).filter(Boolean);
    items.push(
      r.risk.approved
        ? {
            label: "Riesgo",
            text: `El control de riesgo la aprobó${reasons.length ? `: ${reasons.join("; ")}` : " (dentro de tus límites)."}`,
            tone: "good",
          }
        : {
            label: "Riesgo",
            text: `El control de riesgo la rechazó${reasons.length ? `: ${reasons.join("; ")}` : "."}`,
            tone: "bad",
          },
    );
  }

  if (r.text) items.push({ label: "Explicación", text: r.text, tone: "neutral" });
  return items;
}
