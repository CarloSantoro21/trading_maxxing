"""IntradayMomentumAgent: el retorno de la primera media hora predice el de la última.

Evidencia: Shen, Urquhart y Wang, *Bitcoin intraday time-series momentum*, Financial Review
57(2), 2022 (DOI 10.1111/fire.12290), versión cripto del momentum intradía de Gao, Han, Li
y Zhou (JFE 2018). Como BTC opera 24 h, el paper define un "día" de mercado:

- Apertura: la hora en que el volumen da un salto (reloj de volumen); en sus exchanges cae
  entre 9:00 y 9:40 hora del Este de EE. UU. (ET).
- Cierre: 17:00 ET, inicio de la pausa diaria de 60 min de los futuros de BTC en CME.

Retornos del día t (p(T) = precio en el instante T):
- r_onfh = p(apertura + 30 min, t) / p(17:00, t-1) - 1  (noche + primera media hora)
- r_slh  = p(16:30, t) / p(16:00, t) - 1                 (penúltima media hora)
- r_lh   = p(17:00, t) / p(16:30, t) - 1                 (última media hora)

`r_lh` depende con signo positivo de `r_onfh` y con signo negativo de `r_slh`. Las reglas de
timing del paper (ec. 7 y 8) operan solo la última media hora:
- `mode="onfh"`: largo si r_onfh > 0, corto si no.
- `mode="slh"`: largo si r_slh < 0, corto si no.
- `mode="both"`: largo si r_onfh > 0 y r_slh < 0; corto si r_onfh <= 0 y r_slh >= 0;
  fuera en otro caso.

Adaptación a Binance Spot (detalles en `docs/estrategias/intraday-momentum.md`): velas de
30 min de `tm_marketdata`, apertura automática en el slot ET con el mayor salto relativo de
volumen (en Binance sale 9:30 ET), y como no hay cortos, "corto" se vuelve `flat` (USDT).
La señal es `long` solo durante [16:30, 17:00) ET; se compra al cierre de la vela de las
16:00 y se vende a las 17:00. Solo se usan velas cerradas en `ctx.now` (sin look-ahead),
igual que en `trend.py`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from tm_shared.contracts import Direction, Horizon, Signal, Timeframe

from tm_agents.base import AgentContext, BaseAgent, register_strategy
from tm_agents.strategies.trend import closed_bars

PERIOD = pd.Timedelta(minutes=30)
SLOTS_PER_DAY = 48
MARKET_TZ = "America/New_York"
CLOSE_SLOT = 34  # 17:00 ET: la sesión termina al cierre de la vela de las 16:30
LAST_SLOT = CLOSE_SLOT - 1  # 16:30 ET, la vela que se opera
PENULTIMATE_SLOT = CLOSE_SLOT - 2  # 16:00 ET
DEFAULT_OPEN_SLOT = 18  # 9:00 ET, la apertura del paper para Bitstamp y CEX.IO
DEFAULT_OPEN_WINDOW = ("00:00", "15:00")
MODES = ("onfh", "slh", "both")


def parse_slot(value: int | str) -> int:
    """`"HH:MM"` (media hora ET) o un entero 0..47 -> número de slot de 30 min."""
    if isinstance(value, str):
        try:
            hh, mm = (int(x) for x in value.split(":"))
        except ValueError as exc:
            raise ValueError(f"hora inválida: {value!r} (usa 'HH:MM' o 0..47)") from exc
        if not (0 <= hh < 24 and mm in (0, 30)):
            raise ValueError(f"la hora debe caer en una media hora: {value!r}")
        return hh * 2 + mm // 30
    slot = int(value)
    if not 0 <= slot < SLOTS_PER_DAY:
        raise ValueError(f"el slot debe estar entre 0 y {SLOTS_PER_DAY - 1}")
    return slot


def slot_label(slot: int) -> str:
    return f"{slot // 2:02d}:{(slot % 2) * 30:02d}"


def _market_index(bars: pd.DataFrame) -> pd.DatetimeIndex:
    """Índice de apertura de las velas en hora ET (las velas sin zona se asumen UTC)."""
    idx = pd.DatetimeIndex(bars.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    return idx.tz_convert(MARKET_TZ)


def _slots(idx: pd.DatetimeIndex) -> np.ndarray:
    return np.asarray(idx.hour * 2 + idx.minute // 30)


def volume_profile(bars: pd.DataFrame) -> pd.Series:
    """Participación promedio de cada slot ET (0..47) en el volumen de su día calendario ET.

    Se normaliza por día para que la tendencia de largo plazo del volumen no pese.
    """
    idx = _market_index(bars)
    vol = pd.Series(bars["volume"].to_numpy(dtype=float), index=idx)
    total = vol.groupby(idx.normalize()).transform("sum")
    share = vol / total.where(total > 0)
    valid = share.notna().to_numpy()
    return share[valid].groupby(_slots(idx)[valid]).mean().reindex(range(SLOTS_PER_DAY))


def volume_clock_open(
    bars: pd.DataFrame,
    window: tuple[int | str, int | str] = DEFAULT_OPEN_WINDOW,
    default: int = DEFAULT_OPEN_SLOT,
) -> int:
    """Slot ET donde "abre" el día: el mayor salto relativo de volumen contra el slot previo.

    Solo se buscan slots dentro de `window` (inclusivo), que debe quedar antes del cierre de
    las 17:00 ET; si no hay datos suficientes se devuelve `default`.
    """
    lo, hi = parse_slot(window[0]), parse_slot(window[1])
    if not lo <= hi < PENULTIMATE_SLOT:
        raise ValueError("la ventana de apertura debe terminar antes de las 16:00 ET")
    profile = volume_profile(bars)
    jump = profile / profile.shift(1, fill_value=profile.iloc[-1])
    candidates = jump.loc[lo:hi].replace([np.inf, -np.inf], np.nan).dropna()
    if candidates.empty:
        return default
    return int(candidates.idxmax())


def session_table(bars: pd.DataFrame, open_slot: int) -> pd.DataFrame:
    """Una fila por día calendario ET con los retornos del paper.

    Columnas `r_onfh`, `r_slh`, `r_lh` y `first_volume` (volumen de la primera media hora);
    un retorno es NaN si falta alguna de las velas que lo definen. El índice es la fecha ET.
    """
    if not 0 <= open_slot < PENULTIMATE_SLOT:
        raise ValueError("la apertura debe quedar antes de las 16:00 ET")
    idx = _market_index(bars)
    df = pd.DataFrame(
        {
            "date": idx.normalize().tz_localize(None),
            "slot": _slots(idx),
            "close": bars["close"].to_numpy(dtype=float),
            "volume": bars["volume"].to_numpy(dtype=float),
        }
    ).drop_duplicates(["date", "slot"])
    close = df.pivot(index="date", columns="slot", values="close")
    vol = df.pivot(index="date", columns="slot", values="volume")
    close = close.reindex(columns=range(SLOTS_PER_DAY))
    vol = vol.reindex(columns=range(SLOTS_PER_DAY))
    close = close.asfreq("D")
    vol = vol.reindex(close.index)

    # p(T) es el cierre de la vela que termina en T, es decir la del slot anterior a T.
    p_open30 = close[open_slot]
    p_1600 = close[PENULTIMATE_SLOT - 1]
    p_1630 = close[PENULTIMATE_SLOT]
    p_1700 = close[LAST_SLOT]
    out = pd.DataFrame(index=close.index)
    out["r_onfh"] = p_open30 / p_1700.shift(1) - 1.0
    out["r_slh"] = p_1630 / p_1600 - 1.0
    out["r_lh"] = p_1700 / p_1630 - 1.0
    out["first_volume"] = vol[open_slot]
    out.index.name = "session_date"
    return out


def timing_position(r_onfh: float, r_slh: float, mode: str) -> int:
    """Posición del paper para la última media hora: +1 largo, -1 corto, 0 fuera."""
    if mode not in MODES:
        raise ValueError(f"mode debe ser uno de {MODES}")
    if mode == "onfh":
        return 0 if not np.isfinite(r_onfh) else (1 if r_onfh > 0 else -1)
    if mode == "slh":
        return 0 if not np.isfinite(r_slh) else (1 if r_slh < 0 else -1)
    if not (np.isfinite(r_onfh) and np.isfinite(r_slh)):
        return 0
    if r_onfh > 0 and r_slh < 0:
        return 1
    if r_onfh <= 0 and r_slh >= 0:
        return -1
    return 0


def rule_is_long(r_onfh: float, r_slh: float, mode: str = "onfh", threshold: float = 0.0) -> bool:
    """Regla spot: solo la pata larga del paper; `threshold` exige un r_onfh mínimo."""
    if timing_position(r_onfh, r_slh, mode) != 1:
        return False
    return mode == "slh" or r_onfh > threshold


@dataclass(frozen=True)
class FeeCheck:
    """Chequeo rápido de la regla sobre datos históricos (no es el backtest formal del #13)."""

    days: int
    trades: int
    years: float
    trades_per_year: float
    hit_rate: float | None
    mean_gross_per_trade: float | None
    mean_net_per_trade: float | None
    fee_per_trade: float
    gross_annual: float
    fees_annual: float
    net_annual: float
    breakeven_fee_per_side: float | None
    beta_onfh: float | None
    beta_slh: float | None
    r2: float | None


def _ols(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, float] | None:
    """Coeficientes [a, b1, ...] y R² de `y = a + X b`; None si la matriz es singular."""
    design = np.column_stack([np.ones(len(y)), x])
    if len(y) <= design.shape[1] or np.linalg.matrix_rank(design) < design.shape[1]:
        return None
    coef, *_ = np.linalg.lstsq(design, y, rcond=None)
    resid = y - design @ coef
    r2 = float(1.0 - resid.var() / y.var()) if y.var() > 0 else float("nan")
    return coef, r2


def fee_check(
    sessions: pd.DataFrame,
    fee_per_side: float = 0.001,
    mode: str = "onfh",
    threshold: float = 0.0,
) -> FeeCheck:
    """Cuántas operaciones (spot, solo largo) genera la regla y cuánto se llevan las comisiones.

    Retornos simples sumados (sin capitalizar) para comparar órdenes de magnitud; cada
    operación paga `fee_per_side` al comprar y al vender. También estima la regresión
    `r_lh = a + b1 r_onfh + b2 r_slh` (ec. 5 del paper) sobre los días completos.
    """
    days = sessions.dropna(subset=["r_onfh", "r_slh", "r_lh"])
    if days.empty:
        raise ValueError("no hay días completos para el chequeo")
    long = np.array(
        [
            rule_is_long(f, s, mode, threshold)
            for f, s in zip(days["r_onfh"], days["r_slh"], strict=True)
        ]
    )
    gross = days["r_lh"].to_numpy()[long]
    fee = 2.0 * fee_per_side
    span = (days.index[-1] - days.index[0]) + pd.Timedelta(days=1)
    years = span / pd.Timedelta(days=365.25)
    n = int(long.sum())
    fit = _ols(days[["r_onfh", "r_slh"]].to_numpy(), days["r_lh"].to_numpy())
    mean_gross = float(gross.mean()) if n else None
    return FeeCheck(
        days=len(days),
        trades=n,
        years=float(years),
        trades_per_year=n / years,
        hit_rate=float((gross > 0).mean()) if n else None,
        mean_gross_per_trade=mean_gross,
        mean_net_per_trade=mean_gross - fee if mean_gross is not None else None,
        fee_per_trade=fee,
        gross_annual=float(gross.sum() / years),
        fees_annual=n * fee / years,
        net_annual=float((gross.sum() - n * fee) / years),
        breakeven_fee_per_side=mean_gross / 2.0 if mean_gross is not None else None,
        beta_onfh=float(fit[0][1]) if fit else None,
        beta_slh=float(fit[0][2]) if fit else None,
        r2=fit[1] if fit else None,
    )


@register_strategy("intraday_momentum")
class IntradayMomentumAgent(BaseAgent):
    timeframe = Timeframe.M30

    def __init__(
        self,
        open_time: int | str = "auto",
        mode: str = "onfh",
        threshold: float = 0.0,
        open_window: tuple[int | str, int | str] = DEFAULT_OPEN_WINDOW,
        volume_lookback_days: int = 90,
        min_profile_days: int = 7,
        default_open: int | str = DEFAULT_OPEN_SLOT,
        sigma_lookback_days: int = 90,
        **params: object,
    ) -> None:
        super().__init__(
            open_time=open_time,
            mode=mode,
            threshold=threshold,
            open_window=list(open_window),
            volume_lookback_days=volume_lookback_days,
            **params,
        )
        if mode not in MODES:
            raise ValueError(f"mode debe ser uno de {MODES}")
        if min(volume_lookback_days, min_profile_days, sigma_lookback_days) < 1:
            raise ValueError("los parámetros de días deben ser >= 1")
        self.fixed_open = None if open_time == "auto" else parse_slot(open_time)
        self.mode = mode
        self.threshold = float(threshold)
        self.open_window = (parse_slot(open_window[0]), parse_slot(open_window[1]))
        self.volume_lookback_days = int(volume_lookback_days)
        self.min_profile_days = int(min_profile_days)
        self.default_open = parse_slot(default_open)
        self.sigma_lookback_days = int(sigma_lookback_days)
        for slot in (self.fixed_open, self.default_open):
            if slot is not None and slot >= PENULTIMATE_SLOT:
                raise ValueError("la apertura debe quedar antes de las 16:00 ET")

    def open_slot(self, bars: pd.DataFrame, now: pd.Timestamp) -> tuple[int, str]:
        """Slot ET de apertura y de dónde salió (`fixed`, `volume` o `default`)."""
        if self.fixed_open is not None:
            return self.fixed_open, "fixed"
        idx = pd.DatetimeIndex(bars.index)
        idx = idx.tz_localize("UTC") if idx.tz is None else idx
        window = bars.loc[idx >= now - pd.Timedelta(days=self.volume_lookback_days)]
        if len(window) < self.min_profile_days * SLOTS_PER_DAY:
            return self.default_open, "default"
        return volume_clock_open(window, self.open_window, self.default_open), "volume"

    def analyze(self, ctx: AgentContext) -> Signal:
        now = pd.Timestamp(ctx.now).tz_convert("UTC")
        bars = closed_bars(ctx.bars_for(self.timeframe), now, PERIOD)
        open_slot, open_source = self.open_slot(bars, now)
        now_et = now.tz_convert(MARKET_TZ)
        today = now_et.normalize().tz_localize(None)
        current_slot = now_et.hour * 2 + now_et.minute // 30
        in_last = current_slot == LAST_SLOT

        sessions = session_table(bars, open_slot) if len(bars) else pd.DataFrame()
        row = sessions.loc[today] if today in sessions.index else None
        r_onfh = float(row["r_onfh"]) if row is not None else float("nan")
        r_slh = float(row["r_slh"]) if row is not None else float("nan")
        paper_pos = timing_position(r_onfh, r_slh, self.mode)
        long = in_last and rule_is_long(r_onfh, r_slh, self.mode, self.threshold)

        sigma = self._onfh_sigma(sessions, today)
        if long:
            ref = abs(r_slh) if self.mode == "slh" else abs(r_onfh)
            confidence = 1.0 if sigma is None else float(min(1.0, ref / sigma))
            confidence = max(confidence, 1e-6)
        else:
            confidence = 0.0

        if not in_last:
            reason = "fuera de la última media hora (16:30-17:00 ET)"
        elif paper_pos == 0 and not (np.isfinite(r_onfh) and np.isfinite(r_slh)):
            reason = "faltan velas para calcular la señal"
        elif long:
            reason = "la regla da largo en la última media hora"
        elif paper_pos == -1:
            reason = "el paper iría en corto; en spot se queda en USDT"
        else:
            reason = "la regla no se cumple"

        close_et = (now_et.normalize() + PERIOD * CLOSE_SLOT).tz_convert("UTC")
        return Signal(
            symbol=ctx.symbol,
            direction=Direction.LONG if long else Direction.FLAT,
            confidence=confidence,
            horizon=Horizon.SHORT,
            timeframe=self.timeframe,
            source=self.name,
            generated_at=now.to_pydatetime(),
            metadata={
                "open_slot_et": open_slot,
                "open_time_et": slot_label(open_slot),
                "open_source": open_source,
                "session_date": today.date().isoformat(),
                "current_slot_et": current_slot,
                "r_onfh": r_onfh if np.isfinite(r_onfh) else None,
                "r_slh": r_slh if np.isfinite(r_slh) else None,
                "paper_position": paper_pos if in_last else 0,
                "onfh_sigma": sigma,
                "mode": self.mode,
                "reason": reason,
                "exit_at": close_et.isoformat() if long else None,
                "bars_used": len(bars),
                "last_close": float(bars["close"].iloc[-1]) if len(bars) else None,
            },
        )

    def _onfh_sigma(self, sessions: pd.DataFrame, today: pd.Timestamp) -> float | None:
        """Desviación estándar de r_onfh en los días previos (escala la confianza)."""
        if sessions.empty:
            return None
        past = sessions.loc[sessions.index < today, "r_onfh"].dropna()
        past = past.tail(self.sigma_lookback_days)
        if len(past) < 2:
            return None
        sigma = float(past.std(ddof=1))
        return sigma if sigma > 0 else None
