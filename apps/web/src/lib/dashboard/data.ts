import type { SupabaseClient } from "@supabase/supabase-js";
import type { BotStatus } from "@/lib/api/client";
import { asRationale } from "@/lib/dashboard/rationale";
import { toNumber } from "@/lib/format";

/**
 * Datos del dashboard (#23), leídos con supabase-js y la sesión del usuario.
 *
 * RLS limita cada consulta a las filas del usuario (profiles -> bots -> trades y
 * equity_snapshots), así que aquí no hay filtros de seguridad, solo de forma. Las
 * actualizaciones en vivo llegan por Supabase Realtime y se aplican con `dashboardReducer`.
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

export class DashboardLoadError extends Error {
  constructor(message: string, options?: { cause?: unknown }) {
    super(message, options);
    this.name = "DashboardLoadError";
  }
}

export const NO_SLEEVE = "sin_sleeve";

export function sleeveOf(trade: Pick<TradeRow, "rationale">): string {
  const sleeve = asRationale(trade.rationale).sleeve;
  return typeof sleeve === "string" && sleeve ? sleeve : NO_SLEEVE;
}

function check<T>(res: { data: T | null; error: unknown }, what: string): T {
  if (res.error) throw new DashboardLoadError(`No se pudo leer ${what}.`, { cause: res.error });
  return (res.data ?? ([] as unknown as T)) as T;
}

const TRADE_COLUMNS =
  "id, bot_id, symbol, side, quantity, entry_price, exit_price, stop_loss, take_profit, fees, pnl, rationale, opened_at, closed_at";

export async function fetchDashboard(supabase: SupabaseClient, userId: string): Promise<DashboardData> {
  const profileRes = await supabase
    .from("profiles")
    .select("id, capital, risk_level, max_drawdown")
    .eq("user_id", userId)
    .maybeSingle();
  if (profileRes.error) {
    throw new DashboardLoadError("No se pudo leer tu perfil.", { cause: profileRes.error });
  }
  const raw = profileRes.data as { id: string; capital: unknown; risk_level: unknown; max_drawdown: unknown } | null;
  if (!raw) return EMPTY_DASHBOARD;
  const profile: ProfileSummary = {
    id: raw.id,
    capital: toNumber(raw.capital) ?? 0,
    risk_level: toNumber(raw.risk_level) ?? 3,
    max_drawdown: toNumber(raw.max_drawdown),
  };

  const bots = check<BotRow[]>(
    await supabase
      .from("bots")
      .select("id, profile_id, name, status, started_at, stopped_at")
      .eq("profile_id", profile.id)
      .order("created_at", { ascending: true }),
    "tus bots",
  );
  const ids = bots.map((b) => b.id);
  if (ids.length === 0) return { ...EMPTY_DASHBOARD, profile, bots };

  const [snapRes, recentRes, openRes, closedRes] = await Promise.all([
    supabase
      .from("equity_snapshots")
      .select("bot_id, ts, equity, cash, positions_value, drawdown")
      .in("bot_id", ids)
      .order("ts", { ascending: false })
      .limit(MAX_SNAPSHOTS),
    supabase
      .from("trades")
      .select(TRADE_COLUMNS)
      .in("bot_id", ids)
      .order("opened_at", { ascending: false })
      .limit(RECENT_TRADES),
    supabase
      .from("trades")
      .select(TRADE_COLUMNS)
      .in("bot_id", ids)
      .is("closed_at", null)
      .order("opened_at", { ascending: false }),
    supabase
      .from("trades")
      .select("id, pnl, sleeve:rationale->>sleeve")
      .in("bot_id", ids)
      .not("closed_at", "is", null),
  ]);

  const snapshots = [...check<SnapshotRow[]>(snapRes, "tu curva de equity")].reverse();
  const recentTrades = check<TradeRow[]>(recentRes, "tus operaciones");
  const openTrades = check<TradeRow[]>(openRes, "tus posiciones");
  const closedRows = check<{ id: string; pnl: unknown; sleeve: string | null }[]>(closedRes, "tu P&L");
  const closed: DashboardData["closed"] = {};
  for (const row of closedRows) {
    closed[row.id] = { sleeve: row.sleeve || NO_SLEEVE, pnl: toNumber(row.pnl) ?? 0 };
  }
  return { profile, bots, snapshots, recentTrades, openTrades, closed };
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

// --- Realtime ------------------------------------------------------------------------

export type DashboardAction =
  | { type: "loaded"; data: DashboardData }
  | { type: "trade"; row: TradeRow }
  | { type: "snapshot"; row: SnapshotRow }
  | { type: "bot"; row: BotRow }
  | { type: "botDeleted"; id: string };

function byOpenedDesc(a: TradeRow, b: TradeRow) {
  return Date.parse(b.opened_at) - Date.parse(a.opened_at);
}

function upsert<T extends { id: string }>(rows: T[], row: T): T[] {
  const i = rows.findIndex((r) => r.id === row.id);
  if (i === -1) return [...rows, row];
  const next = [...rows];
  next[i] = { ...next[i], ...row };
  return next;
}

/** Aplica un cambio de Realtime (o una respuesta de la API) al estado del dashboard. */
export function dashboardReducer(state: DashboardData, action: DashboardAction): DashboardData {
  switch (action.type) {
    case "loaded":
      return action.data;
    case "trade": {
      const row = action.row;
      if (!state.bots.some((b) => b.id === row.bot_id)) return state;
      const recentTrades = upsert(state.recentTrades, row).sort(byOpenedDesc).slice(0, RECENT_TRADES);
      const merged = recentTrades.find((t) => t.id === row.id) ?? row;
      const isOpen = merged.closed_at === null || merged.closed_at === undefined;
      const openTrades = isOpen
        ? upsert(state.openTrades, row).sort(byOpenedDesc)
        : state.openTrades.filter((t) => t.id !== row.id);
      const closed = { ...state.closed };
      if (!isOpen) closed[row.id] = { sleeve: sleeveOf(merged), pnl: toNumber(merged.pnl) ?? 0 };
      return { ...state, recentTrades, openTrades, closed };
    }
    case "snapshot": {
      const row = action.row;
      if (!state.bots.some((b) => b.id === row.bot_id)) return state;
      if (state.snapshots.some((s) => s.bot_id === row.bot_id && s.ts === row.ts)) return state;
      return { ...state, snapshots: [...state.snapshots, row].slice(-MAX_SNAPSHOTS) };
    }
    case "bot": {
      const row = action.row;
      if (!state.profile || row.profile_id !== state.profile.id) return state;
      return { ...state, bots: upsert(state.bots, row) };
    }
    case "botDeleted":
      return { ...state, bots: state.bots.filter((b) => b.id !== action.id) };
  }
}
