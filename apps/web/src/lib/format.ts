/**
 * Formato de números, fechas y nombres para el dashboard y la página de estrategias.
 *
 * Postgres devuelve `numeric` como texto en PostgREST, así que todo pasa por `toNumber`.
 * Las fechas se muestran en la zona horaria del navegador.
 */

export function toNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

/** 1234.5 -> "1,234.50" (USDT, dos decimales). */
export function formatMoney(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return value.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

/** Igual que formatMoney pero con signo explícito (+12.30 / -4.00). */
export function formatSignedMoney(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  return `${sign}${formatMoney(Math.abs(value))}`;
}

/** Fracción a porcentaje: 0.1234 -> "12.3 %". */
export function formatPct(value: number | null | undefined, digits = 1, signed = false): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const pct = value * 100;
  const sign = signed && pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(digits)} %`;
}

/** Cantidad de cripto con hasta 6 decimales. */
export function formatQty(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return value.toLocaleString("en-US", { maximumFractionDigits: 6 });
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("es-MX", { dateStyle: "medium", timeStyle: "short" });
}

export function formatDay(iso: string | null | undefined): string {
  if (!iso) return "—";
  // Un día "YYYY-MM-DD" se interpreta en UTC; se muestra tal cual para no correrlo de fecha.
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  const d = m ? new Date(Date.UTC(+m[1], +m[2] - 1, +m[3])) : new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("es-MX", { day: "numeric", month: "short", ...(m ? { timeZone: "UTC" } : {}) });
}

/** "BTCUSDT" -> "BTC/USDT". */
export function formatSymbol(symbol: string): string {
  return symbol.endsWith("USDT") ? `${symbol.slice(0, -4)}/USDT` : symbol;
}

/** Nombre legible de cada estrategia de la biblioteca (fallback: el identificador). */
const STRATEGY_LABELS: Record<string, string> = {
  trend: "Tendencia",
  intraday_momentum: "Momentum intradía",
  kronos: "Kronos (pronóstico)",
};

export function strategyLabel(name: string): string {
  return STRATEGY_LABELS[name] ?? name;
}

export type StrategyState = "research" | "lab" | "shadow" | "active" | "probation" | "retired";

export const STATE_LABELS: Record<StrategyState, string> = {
  research: "Investigación",
  lab: "Laboratorio",
  shadow: "Shadow",
  active: "Activa",
  probation: "En observación",
  retired: "Retirada",
};

export function stateLabel(state: string): string {
  return STATE_LABELS[state as StrategyState] ?? state;
}

export const BOT_STATUS_LABELS: Record<string, string> = {
  running: "Operando",
  stopped: "Detenido",
  paused: "En pausa",
  killed: "Paro de emergencia",
};

export function botStatusLabel(status: string): string {
  return BOT_STATUS_LABELS[status] ?? status;
}
