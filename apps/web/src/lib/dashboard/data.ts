import type { BotStatus } from "@/lib/api/client";
import { asRationale } from "@/lib/dashboard/rationale";
import { toNumber } from "@/lib/format";

/**
 * Datos del dashboard (#23): `GET /api/dashboard` devuelve todo en una llamada, ya
 * filtrado por el usuario de la sesión (la API reemplaza a RLS). El dashboard la vuelve a
 * pedir cada pocos segundos (polling con SWR).
 *
 * Cada bot es el portafolio de un usuario y cada estrategia es un sleeve dentro de él; la
 * tabla trades no tiene columna de sleeve: va en `rationale.sleeve` (lo escribe el engine).
 */

export const RECENT_TRADES = 20;
export const MAX_SNAPSHOTS = 2000;

export interface ProfileSummary {
  id: string;
  capital: number;
  risk_level: number;
  max_drawdown: number | null;
}

export interface BotRow {
  id: string;
  profile_id: string;
  name: string;
  status: BotStatus;
  started_at: string | null;
  stopped_at: string | null;
}

export interface SnapshotRow {
  bot_id: string;
  ts: string;
  equity: number | string;
  cash?: number | string;
  positions_value?: number | string;
  drawdown?: number | string;
}

export interface TradeRow {
  id: string;
  bot_id: string;
  symbol: string;
  side: "buy" | "sell";
  quantity: number | string;
  entry_price: number | string;
  exit_price: number | string | null;
  stop_loss: number | string | null;
  take_profit: number | string | null;
  fees: number | string;
  pnl: number | string | null;
  rationale: unknown;
  opened_at: string;
  closed_at: string | null;
}

export interface DashboardData {
  profile: ProfileSummary | null;
  bots: BotRow[];
  snapshots: SnapshotRow[];
  /** Últimas operaciones (abiertas o cerradas), de la más reciente a la más antigua. */
  recentTrades: TradeRow[];
  /** Todas las operaciones abiertas (las posiciones). */
  openTrades: TradeRow[];
  /** P&L realizado de cada operación cerrada, por id. */
  closed: Record<string, { sleeve: string; pnl: number }>;
}

export const EMPTY_DASHBOARD: DashboardData = {
  profile: null,
  bots: [],
  snapshots: [],
  recentTrades: [],
  openTrades: [],
  closed: {},
};

/** Llave de SWR (ruta de la API) del dashboard. */
export const DASHBOARD_KEY = "/dashboard";

export const NO_SLEEVE = "sin_sleeve";

export function sleeveOf(trade: Pick<TradeRow, "rationale">): string {
  const sleeve = asRationale(trade.rationale).sleeve;
  return typeof sleeve === "string" && sleeve ? sleeve : NO_SLEEVE;
}

// --- cálculos ------------------------------------------------------------------------

export interface EquityPoint {
  ts: string;
  /** Milisegundos desde epoch (eje X de la gráfica). */
  t: number;
  equity: number;
}

/**
 * Curva de equity del portafolio: suma de la última foto de cada bot en cada instante.
 * Con un solo bot es su curva tal cual; con varios, cada bot conserva su último valor
 * hasta que escribe uno nuevo.
 */
export function portfolioEquity(snapshots: SnapshotRow[]): EquityPoint[] {
  const sorted = [...snapshots].sort((a, b) => Date.parse(a.ts) - Date.parse(b.ts));
  const latest = new Map<string, number>();
  const points: EquityPoint[] = [];
  for (const s of sorted) {
    const equity = toNumber(s.equity);
    const t = Date.parse(s.ts);
    if (equity === null || Number.isNaN(t)) continue;
    latest.set(s.bot_id, equity);
    const total = [...latest.values()].reduce((a, b) => a + b, 0);
    const last = points[points.length - 1];
    if (last && last.t === t) last.equity = total;
    else points.push({ ts: s.ts, t, equity: total });
  }
  return points;
}

export interface PortfolioSummary {
  capital: number;
  equity: number;
  pnl: number;
  totalReturn: number;
  realizedPnl: number;
  /** Caída actual desde el máximo de la curva, como fracción. */
  drawdown: number;
  asOf: string | null;
}

export function summarize(data: DashboardData): PortfolioSummary {
  const capital = data.profile?.capital ?? 0;
  const curve = portfolioEquity(data.snapshots);
  const last = curve[curve.length - 1];
  const equity = last ? last.equity : capital;
  const peak = curve.reduce((m, p) => Math.max(m, p.equity), capital);
  const realizedPnl = Object.values(data.closed).reduce((a, c) => a + c.pnl, 0);
  return {
    capital,
    equity,
    pnl: equity - capital,
    totalReturn: capital ? (equity - capital) / capital : 0,
    realizedPnl,
    drawdown: peak > 0 ? Math.max(0, 1 - equity / peak) : 0,
    asOf: last?.ts ?? null,
  };
}

export interface SleevePosition {
  id: string;
  symbol: string;
  side: "buy" | "sell";
  quantity: number;
  entryPrice: number;
  /** Costo de entrada en USDT (cantidad × precio). */
  cost: number;
  stopLoss: number | null;
  takeProfit: number | null;
  openedAt: string;
}

export interface SleeveSummary {
  sleeve: string;
  positions: SleevePosition[];
  invested: number;
  realizedPnl: number;
  closedTrades: number;
}

/** Posiciones abiertas y P&L realizado agrupados por sleeve (estrategia). */
export function sleeves(data: DashboardData): SleeveSummary[] {
  const map = new Map<string, SleeveSummary>();
  const get = (name: string) => {
    let s = map.get(name);
    if (!s) {
      s = { sleeve: name, positions: [], invested: 0, realizedPnl: 0, closedTrades: 0 };
      map.set(name, s);
    }
    return s;
  };
  for (const t of data.openTrades) {
    const quantity = toNumber(t.quantity) ?? 0;
    const entryPrice = toNumber(t.entry_price) ?? 0;
    const s = get(sleeveOf(t));
    s.positions.push({
      id: t.id,
      symbol: t.symbol,
      side: t.side,
      quantity,
      entryPrice,
      cost: quantity * entryPrice,
      stopLoss: toNumber(t.stop_loss),
      takeProfit: toNumber(t.take_profit),
      openedAt: t.opened_at,
    });
    s.invested += quantity * entryPrice;
  }
  for (const c of Object.values(data.closed)) {
    const s = get(c.sleeve);
    s.realizedPnl += c.pnl;
    s.closedTrades += 1;
  }
  return [...map.values()].sort((a, b) => a.sleeve.localeCompare(b.sleeve));
}

// --- respuestas de la API -------------------------------------------------------------

/**
 * Aplica el bot que devuelve la API (crear, arrancar, detener, paro) sin esperar al
 * siguiente refresco. Ignora bots de otro perfil.
 */
export function withBot(state: DashboardData, bot: BotRow): DashboardData {
  if (!state.profile || bot.profile_id !== state.profile.id) return state;
  const i = state.bots.findIndex((b) => b.id === bot.id);
  if (i === -1) return { ...state, bots: [...state.bots, bot] };
  const bots = [...state.bots];
  bots[i] = { ...bots[i], ...bot };
  return { ...state, bots };
}
