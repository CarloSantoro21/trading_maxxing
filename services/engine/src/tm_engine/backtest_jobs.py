"""Backtests encolados desde el frontend: los toma el motor de la Mac y escribe el reporte.

El usuario crea el backtest con ``POST /api/backtests`` (queda ``queued`` en D1). Ya no hay
stream ``tm:backtests``: :class:`BacktestJobRunner` consulta
``GET /internal/backtests?status=queued`` a lo más cada ``interval`` segundos (uno por minuto
por defecto) y, por cada job:

1. ``PATCH /internal/backtests/{id}`` con ``status=running`` y ``started_at``;
2. corre ``run(config)`` (por defecto :func:`run_backtest_config`, el simulador vectorizado del
   laboratorio con los datos de ``tm_marketdata``);
3. ``PATCH`` con ``status=done``, ``report`` y ``finished_at``; o ``status=failed`` con
   ``error`` si algo falló. La base exige reporte en ``done`` y error en ``failed``.

Si la Mac se apaga con un job en ``running``, al volver se queda así: la API no lo reencola.
Se ve en el frontend y se puede volver a pedir.

Configuración de un job (``config`` de ``POST /api/backtests``)::

    {"symbols": ["BTCUSDT"], "timeframe": "15m", "start": "2025-01-01", "end": "2025-06-30",
     "mode": "walk_forward", "strategies": ["trend"], "initial_capital": 10000}

El reporte trae ``metrics`` (retorno total, CAGR, Sharpe, volatilidad, drawdown máximo),
``benchmark`` (comprar y mantener), ``trades``, ``equity`` diaria y, por estrategia, sus
métricas. Las estrategias se simulan con sus parámetros por defecto (el centro de su
cuadrícula); el walk-forward completo sigue siendo el CLI del laboratorio (``tm_engine.lab``).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import pandas as pd
from tm_shared.api_client import WorkerApiClient, WorkerApiError

log = logging.getLogger(__name__)

DEFAULT_STRATEGIES = ("trend",)
DEFAULT_CAPITAL = 10_000.0
DEFAULT_FEE = 0.001
#: Puntos de equity diaria que se guardan en el reporte (el resto se submuestrea).
MAX_EQUITY_POINTS = 400

BacktestFn = Callable[[Mapping[str, Any]], Mapping[str, Any]]
Loader = Callable[[str, str, pd.Timestamp, pd.Timestamp], pd.DataFrame]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class BacktestJobRunner:
    """Toma los backtests ``queued`` de la API, los corre y escribe el resultado.

    ``poll()`` se llama en cada vuelta del bucle del motor; solo consulta cada ``interval``
    segundos y corre los jobs en el mismo hilo (uno por uno). Nunca lanza.
    """

    def __init__(
        self,
        client: WorkerApiClient,
        run: BacktestFn | None = None,
        *,
        interval: float = 60.0,
        limit: int = 5,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.run = run or run_backtest_config
        self.interval = interval
        self.limit = limit
        self._clock = clock
        self._last = float("-inf")
        self.done = 0
        self.failed = 0

    def start(self, stop: threading.Event, *, tick: float = 5.0) -> threading.Thread:
        """Corre ``poll()`` en un hilo daemon hasta que ``stop`` se active.

        Un backtest largo no debe frenar el latido ni el healthcheck del motor.
        """

        def loop() -> None:
            while not stop.is_set():
                try:
                    self.poll()
                except Exception:  # defensa: el hilo de backtests nunca muere
                    log.exception("falló el ciclo de backtests")
                stop.wait(tick)

        thread = threading.Thread(target=loop, name="tm-backtests", daemon=True)
        thread.start()
        return thread

    def poll(self) -> int:
        """Corre los jobs pendientes si ya toca; devuelve cuántos procesó."""
        now = self._clock()
        if now - self._last < self.interval:
            return 0
        self._last = now
        try:
            jobs = self.client.get("/internal/backtests", {"status": "queued", "limit": self.limit})
        except WorkerApiError as exc:
            log.warning("no se pudieron leer los backtests encolados: %s", exc)
            return 0
        count = 0
        for job in jobs or []:
            if self.process(job):
                count += 1
        return count

    def process(self, job: Mapping[str, Any]) -> bool:
        """Corre un job. ``False`` si no se pudo marcar como ``running`` (otro lo tomó)."""
        job_id = str(job["id"])
        try:
            self.client.patch(
                f"/internal/backtests/{job_id}", {"status": "running", "started_at": _now_iso()}
            )
        except WorkerApiError as exc:
            log.warning("no se pudo tomar el backtest %s: %s", job_id, exc)
            return False
        log.info("backtest %s: corriendo", job_id)
        try:
            report = dict(self.run(job.get("config") or {}))
        except Exception as exc:
            self.failed += 1
            error = f"{type(exc).__name__}: {exc}"[:2000]
            log.warning("backtest %s falló: %s", job_id, error)
            self._finish(job_id, {"status": "failed", "error": error})
            return True
        self.done += 1
        self._finish(job_id, {"status": "done", "report": report})
        log.info("backtest %s: listo", job_id)
        return True

    def _finish(self, job_id: str, body: dict[str, Any]) -> None:
        try:
            self.client.patch(f"/internal/backtests/{job_id}", {**body, "finished_at": _now_iso()})
        except WorkerApiError as exc:
            log.error("no se pudo guardar el resultado del backtest %s: %s", job_id, exc)


# --- corrida por defecto ---------------------------------------------------------------------


def market_loader(data_dir: str | None = None) -> Loader:
    """Velas de ``tm_marketdata`` (descarga de Binance y caché en parquet)."""
    from tm_marketdata.service import MarketDataService

    service = MarketDataService(data_dir=data_dir)
    return lambda symbol, tf, start, end: service.get_bars(symbol, tf, start, end)


def run_backtest_config(
    config: Mapping[str, Any],
    *,
    loader: Loader | None = None,
    fee: float = DEFAULT_FEE,
) -> dict[str, Any]:
    """Simula ``config`` con el simulador del laboratorio y arma el reporte (ver el módulo)."""
    from tm_engine.lab.metrics import perf_stats
    from tm_engine.lab.simulator import buy_and_hold, daily_equity, simulate_sleeve, slice_bars
    from tm_engine.lab.strategies import get_adapter

    symbols = [str(s).upper() for s in config.get("symbols") or []]
    if not symbols:
        raise ValueError("el backtest necesita al menos un símbolo")
    start = pd.Timestamp(config["start"], tz="UTC")
    end = pd.Timestamp(config["end"], tz="UTC")
    if end <= start:
        raise ValueError("end debe ser posterior a start")
    names = [str(s) for s in config.get("strategies") or []] or list(DEFAULT_STRATEGIES)
    capital = float(config.get("initial_capital") or DEFAULT_CAPITAL)
    loader = loader or market_loader()

    adapters = [get_adapter(name) for name in names]
    share = capital / len(adapters)
    runs: list[Any] = []
    per_strategy: dict[str, Any] = {}
    for adapter in adapters:
        tf = adapter.timeframe.value
        # Historia previa para que los indicadores (Donchian de hasta ~100 días) arranquen.
        warmup = start - pd.Timedelta(days=400)
        frames = {s: _frame(loader(s, tf, warmup, end), s, tf) for s in symbols}
        grid = adapter.default_grid()
        params = grid[len(grid) // 2]
        strategy_runs = []
        sub_share = share / len(frames) / adapter.n_sub_sleeves
        for symbol, frame in frames.items():
            fitted = adapter.fit(params, frame[frame.index < start])
            bars = slice_bars(frame, start, end, adapter.period)
            for k, target in enumerate(adapter.targets(frame, fitted)):
                strategy_runs.append(
                    simulate_sleeve(
                        bars,
                        target,
                        capital=sub_share,
                        fee=fee,
                        period=adapter.period,
                        sleeve=f"{adapter.name}_{k}",
                        symbol=symbol,
                    )
                )
        equity = daily_equity(strategy_runs, start, end)
        per_strategy[adapter.name] = {
            "params": fitted,
            "timeframe": tf,
            "metrics": _metrics(perf_stats(equity)),
            "trades": sum(len(r.trades) for r in strategy_runs),
        }
        runs.extend(strategy_runs)

    equity = daily_equity(runs, start, end)
    period = adapters[0].period
    bench_frames = {
        s: _frame(loader(s, adapters[0].timeframe.value, start, end), s, "") for s in symbols
    }
    bench = daily_equity(
        buy_and_hold(bench_frames, capital=capital, fee=fee, period=period, start=start, end=end),
        start,
        end,
    )
    return {
        "engine": "tm_engine.lab.simulator",
        "mode": config.get("mode") or "walk_forward",
        "symbols": symbols,
        "strategies": names,
        "start": start.date().isoformat(),
        "end": end.date().isoformat(),
        "initial_capital": capital,
        "fee": fee,
        "metrics": _metrics(perf_stats(equity)),
        "benchmark": {"name": "buy_and_hold", "metrics": _metrics(perf_stats(bench))},
        "trades": sum(len(r.trades) for r in runs),
        "by_strategy": per_strategy,
        "equity": _equity_points(equity),
        "generated_at": _now_iso(),
    }


def _frame(frame: pd.DataFrame, symbol: str, tf: str) -> pd.DataFrame:
    if frame is None or frame.empty:
        raise ValueError(f"no hay velas de {tf or 'la temporalidad'} de {symbol} en el rango")
    frame = frame.sort_index()
    idx = pd.DatetimeIndex(frame.index)
    if idx.tz is None:
        frame = frame.set_axis(idx.tz_localize("UTC"))
    return frame


def _metrics(stats: Any) -> dict[str, Any]:
    out = stats.to_dict()
    return {k: (None if isinstance(v, float) and v != v else v) for k, v in out.items()}


def _equity_points(equity: pd.Series, max_points: int = MAX_EQUITY_POINTS) -> list[dict[str, Any]]:
    step = max(1, -(-len(equity) // max_points))
    points: Sequence[tuple[Any, float]] = list(equity.items())[::step]
    if len(equity) and points[-1][0] != equity.index[-1]:
        points = [*points, (equity.index[-1], float(equity.iloc[-1]))]
    return [
        {"day": pd.Timestamp(ts).date().isoformat(), "equity": round(float(v), 2)}
        for ts, v in points
    ]


__all__ = ["BacktestJobRunner", "market_loader", "run_backtest_config"]
